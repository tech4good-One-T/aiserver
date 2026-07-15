# GHCR에서 EC2로 배포하기

## 배포 흐름

`.github/workflows/cicd.yml`은 PR에서 테스트와 컨테이너 빌드를 검증한다. `main`에
머지되면 이미지를 GHCR에 푸시하고, GitHub OIDC로 AWS 단기 자격증명을 받은 뒤 SSM Run
Command로 EC2에 배포한다. EC2는 외부 SSH 포트를 열 필요가 없다.

배포 이미지는 `latest`가 아니라 빌드 결과의 digest로 지정한다. 새 컨테이너가 Docker
헬스체크를 통과하지 못하면 `deploy/ec2-deploy.sh`가 직전 이미지를 다시 실행한다.

## 현재 Docker 배포의 범위

현재 `Dockerfile`과 GHCR 이미지는 Python 3.12 FastAPI·Pillow·OpenCV·Gemini SDK만 포함한다.
FaceShield 저장소, Python 3.8 conda 환경, CUDA 의존성과 가중치는 포함하지 않고,
`deploy/ec2-deploy.sh`도 호스트 저장소·conda를 마운트하거나 GPU를 컨테이너에 전달하지
않는다.

따라서 이 Docker 배포는 `/health`, `/analyze`, 얼굴이 없는 `/process` 검증에만 적합하다.
얼굴이 있는 `/process`는 FaceShield 없이 `DEEPFAKE_PROTECTION_FAILED`를 반환한다. 해커톤
전체 기능을 시연할 때는 **GPU EC2 호스트에 API Python 3.12 프로세스를 직접 실행**하고,
같은 호스트의 **별도 FaceShield Python 3.8/CUDA conda 환경을 CLI로 호출**한다.
현재 배포 스크립트를 그대로 사용하면 이 전체 기능 구성이 되지 않는다.

## 고정 설정

- AWS 리전: `ap-northeast-2` (서울)
- 외부 포트: `8001`
- 컨테이너 포트: `8000`
- 컨테이너 이름: `aiserver1`
- 런타임 환경 파일: `/opt/aiserver1/.env`
- 보안 그룹: TCP `8001`을 백엔드 또는 VPC의 명시적 `BACKEND_CIDR`에만 허용

값은 같은 이름의 GitHub Repository Variable로 재정의할 수 있다.

## 해커톤 전체 기능 호스트 런타임

공식 FaceShield 저장소의 참고 환경은 Ubuntu 22.04, CUDA 12.4, Python 3.8,
RTX A6000 48GB다. AWS에서는 Ubuntu 22.04 기반 Deep Learning AMI와 48GB GPU 인스턴스를
우선 검토하되, GPU·드라이버·CUDA 호환성은 샘플 1장으로 먼저 확인한다.

1. 공식 저장소 <https://github.com/kuai-lab/iccv25_faceshield>를 별도 경로에 clone한다.
2. 출처와 변경 내역을 검토한 commit SHA로 checkout해 배포 중 임의 변경을 막는다.
3. 공식 `environment.yaml`로 `faceshield` conda 환경을 만든다.
4. 공식 README의 ArcFace `models.zip`을 다운로드해 저장소 `models/`에 압축 해제하고,
   배포 전에 출처·무결성·예상 파일 구조를 확인한다.
5. Stable Diffusion·IP-Adapter 등 실행 중 받는 Hugging Face 가중치를 영구 EBS에
   사전 캐시하고 사용 조건과 출처를 확인한다.
6. Ubuntu 호스트에 FaceShield 스크립트가 사용하는 `bc` 등 기본 유틸리티를 설치한다
   (예: `sudo apt-get update && sudo apt-get install -y bc`).
7. FaceShield `execute.sh`를 샘플 1장으로 먼저 완주해 CUDA, 가중치 경로, 출력 경로와
   캐시를 예열한다.
8. API 저장소에서 `uv sync --frozen`을 수행한 뒤 Python 3.12 프로세스 worker 1개를
   실행한다.

```bash
uv run uvicorn app.main:app \
  --env-file /opt/aiserver1/.env \
  --host 0.0.0.0 \
  --port 8001 \
  --workers 1
```

FaceShield 실행은 탐지된 얼굴 크롭별로 CLI 프로세스와 모델을 다시 로드한다.
다중 얼굴은 크롭 수만큼 순차 호출하므로 매우 느릴 수 있다. 이 구성은 단일 데모용이며
운영 전에는 가중치를 상주 로드하는 전용 GPU worker와 큐로 교체해야 한다.

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

운영 환경변수 파일을 EC2에 생성한다. 실제 키를 문서, Git, 공유 메신저와 쉘
히스토리에 남기지 말고 EC2의 보호된 파일을 직접 편집한다.

```bash
sudo install -d -m 700 /opt/aiserver1
sudo install -m 600 /dev/null /opt/aiserver1/.env
sudoedit /opt/aiserver1/.env
```

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
ALLOWED_STORAGE_HOSTS=example-bucket.s3.ap-northeast-2.amazonaws.com
STORAGE_BUCKET=
MAX_IMAGE_BYTES=10485760
MAX_IMAGE_DIMENSION=4096
STORAGE_TIMEOUT_SECONDS=30
ANALYSIS_TIMEOUT_SECONDS=60
PROCESSING_TIMEOUT_SECONDS=600
GEMINI_API_KEY=<NEW_GEMINI_API_KEY>
GEMINI_MODEL=gemini-2.5-flash
FACESHIELD_REPO_PATH=/opt/faceshield/iccv25_faceshield
FACESHIELD_COMMAND=/opt/miniconda3/bin/conda run --no-capture-output -n faceshield bash execute.sh
HF_HOME=/opt/aiserver1/model-cache/huggingface
```

`ALLOWED_STORAGE_HOSTS`는 scheme·경로·와일드카드 없이 정확한 호스트를 쉼표로 나열한다.
`FACESHIELD_REPO_PATH`와 `FACESHIELD_COMMAND`의 conda 경로는 실제 절대 경로로 바꾸며,
API 프로세스 계정이 저장소, 가중치 캐시와 conda 실행 파일을 읽을 수 있어야 한다.
`GEMINI_API_KEY`는 새로 발급한 키를 직접 설정하고 파일 권한 600을 유지한다.

Amazon Linux 2023 인스턴스에서는 SSM Run Command로 `infra/bootstrap-ec2.sh`를 실행해
Docker 설치·자동 시작과 `APP_ENV`, `LOG_LEVEL`만 든 기본 환경 파일을 생성할 수 있다.
나머지 Gemini·Storage·FaceShield 설정은 위 예시에 맞게 별도로 편집해야 한다. 또한 해당
스크립트는 Amazon Linux 2023용 Docker 부트스트랩이며, Ubuntu 22.04 FaceShield
conda/CUDA 환경을 설치하지 않는다.

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
BACKEND_CIDR='10.0.0.0/16' \
EC2_INSTANCE_ID=i-089aa1e7e65187550 ./infra/bootstrap-aws.sh
```

다른 저장소에 재사용할 때는 실제 subject를 명시한다.

```bash
GITHUB_OIDC_SUBJECT='repo:<OWNER>/<REPOSITORY>:ref:refs/heads/main' \
EC2_INSTANCE_ID='<INSTANCE_ID>' \
BACKEND_CIDR='<BACKEND_OR_VPC_CIDR>' \
./infra/bootstrap-aws.sh
```

기본 보안 그룹은 `sg-02f7c2c49365353df`이며, 스크립트는 지정한 `BACKEND_CIDR`에 대해
TCP `8001` 규칙을 멱등하게 추가한다. `0.0.0.0/0`을 지정하지 않는다. 기존에 공개 규칙이
이미 있다면 이 스크립트가 자동으로 취소하지 않으므로 AWS 콘솔 또는 `revoke-security-group-ingress`로
별도 제거한다. 다른 환경에서는 `SECURITY_GROUP_ID`와 `APP_PORT` 환경변수로 재정의한다.

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
