# GHCR에서 EC2로 배포하기

## 배포 흐름

`.github/workflows/cicd.yml`은 PR에서 테스트와 컨테이너 빌드를 검증한다. `main`에
머지되면 이미지를 GHCR에 푸시하고, GitHub OIDC로 AWS 단기 자격증명을 받은 뒤 SSM Run
Command로 EC2에 배포한다. EC2는 외부 SSH 포트를 열 필요가 없다.

배포 이미지는 `latest`가 아니라 빌드 결과의 digest로 지정한다. 새 컨테이너가 Docker
헬스체크를 통과하지 못하면 `deploy/ec2-deploy.sh`가 직전 이미지를 다시 실행한다.

## 고정 설정

- AWS 리전: `ap-northeast-2` (서울)
- 외부 포트: `8001`
- 컨테이너 포트: `8000`
- 컨테이너 이름: `aiserver1`
- 런타임 환경 파일: `/opt/aiserver1/.env`
- 보안 그룹: TCP `8001`을 `0.0.0.0/0`에 허용

값은 같은 이름의 GitHub Repository Variable로 재정의할 수 있다.

## 1. EC2 준비

EC2 인스턴스에 Docker, AWS CLI, SSM Agent가 설치되어 있어야 한다. 인스턴스 IAM 역할에는
AWS 관리형 정책 `AmazonSSMManagedInstanceCore`와 다음 권한을 추가해 배포 중 생성되는 임시
GHCR 토큰을 읽도록 한다. 사용자 관리 KMS 키로
Parameter Store 값을 암호화했다면 해당 키의 `kms:Decrypt` 권한도 필요하다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:ap-northeast-2:<AWS_ACCOUNT_ID>:parameter/aiserver1/deploy/*"
    }
  ]
}
```

운영 환경변수 파일을 EC2에 생성한다.

```bash
sudo install -d -m 700 /opt/aiserver1
sudo tee /opt/aiserver1/.env >/dev/null <<'EOF'
APP_ENV=production
LOG_LEVEL=INFO
EOF
sudo chmod 600 /opt/aiserver1/.env
```

Amazon Linux 2023 인스턴스에서는 SSM Run Command로 `infra/bootstrap-ec2.sh`를 실행해
Docker 설치·자동 시작과 위 환경 파일 생성을 동일하게 적용할 수 있다.

## 2. GitHub OIDC 역할

AWS IAM에 `https://token.actions.githubusercontent.com` 공급자와 `sts.amazonaws.com`
audience를 등록한다. GitHub Actions가 맡을 역할의 신뢰 정책은 반드시 이 저장소의 `main`
브랜치로 제한한다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:tech4good-One-T@305329624/aiserver@1301218374:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

이 조직은 GitHub OIDC subject에 변경 불가능한 조직·저장소 numeric ID를 포함하도록
커스터마이징되어 있다. 다른 조직이나 저장소에 적용할 때는 CloudTrail의
`AssumeRoleWithWebIdentity` 이벤트에서 실제 subject를 확인한 뒤 신뢰 정책과
`GITHUB_OIDC_SUBJECT`를 같은 값으로 설정한다. 기본 형식의 subject를 사용하는 저장소는
`repo:<OWNER>/<REPOSITORY>:ref:refs/heads/main` 값을 사용한다.

역할에는 대상 인스턴스에만 명령을 보내고 결과를 읽는 다음 정책을 부여한다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript",
        "arn:aws:ec2:ap-northeast-2:<AWS_ACCOUNT_ID>:instance/<EC2_INSTANCE_ID>"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:PutParameter",
        "ssm:DeleteParameter"
      ],
      "Resource": "arn:aws:ssm:ap-northeast-2:<AWS_ACCOUNT_ID>:parameter/aiserver1/deploy/*"
    }
  ]
}
```

워크플로는 `packages: read` 권한의 단기 `GITHUB_TOKEN`을 배포별 SecureString으로 생성한다.
EC2가 이미지를 가져온 뒤 워크플로가 파라미터를 삭제하므로 장기 GHCR PAT는 필요 없다.

저장소의 부트스트랩 스크립트로 위 OIDC 공급자, 두 역할과 인스턴스 프로파일을 동일하게
생성하거나 갱신할 수 있다.

```bash
EC2_INSTANCE_ID=i-089aa1e7e65187550 ./infra/bootstrap-aws.sh
```

다른 저장소에 재사용할 때는 실제 subject를 명시한다.

```bash
GITHUB_OIDC_SUBJECT='repo:<OWNER>/<REPOSITORY>:ref:refs/heads/main' \
EC2_INSTANCE_ID='<INSTANCE_ID>' \
./infra/bootstrap-aws.sh
```

기본 보안 그룹은 `sg-02f7c2c49365353df`이며, 스크립트는 기존 규칙을 유지한 채 TCP
`8001` 공개 규칙을 멱등하게 추가한다. 다른 환경에서는 `SECURITY_GROUP_ID`와 `APP_PORT`
환경변수로 재정의한다.

## 3. GitHub Repository Variables

저장소 `tech4good-One-T/aiserver`에 다음 Variables를 등록한다.

| 이름 | 값 |
| --- | --- |
| `AWS_ROLE_ARN` | 위에서 만든 OIDC 역할 ARN |
| `EC2_INSTANCE_ID` | 배포 대상 인스턴스 ID |
| `AWS_REGION` | `ap-northeast-2` |
| `APP_PORT` | `8001` |

```bash
gh variable set AWS_ROLE_ARN --repo tech4good-One-T/aiserver --body '<ROLE_ARN>'
gh variable set EC2_INSTANCE_ID --repo tech4good-One-T/aiserver --body '<INSTANCE_ID>'
```

설정이 끝나면 `main` 푸시 또는 Actions 화면의 수동 실행으로 배포할 수 있다.
