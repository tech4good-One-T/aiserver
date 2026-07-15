#!/usr/bin/env bash

set -Eeuo pipefail

: "${EC2_INSTANCE_ID:?EC2_INSTANCE_ID environment variable is required}"

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
GITHUB_OIDC_SUBJECT="${GITHUB_OIDC_SUBJECT:-repo:tech4good-One-T@305329624/aiserver@1301218374:ref:refs/heads/main}"
DEPLOY_ROLE_NAME="${DEPLOY_ROLE_NAME:-aiserver1-github-deploy}"
EC2_ROLE_NAME="${EC2_ROLE_NAME:-aiserver1-ec2-ssm}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-aiserver1-ec2-ssm}"
SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-sg-02f7c2c49365353df}"
APP_PORT="${APP_PORT:-8001}"
OIDC_HOST=token.actions.githubusercontent.com

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
OIDC_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"
INSTANCE_ARN="arn:aws:ec2:${AWS_REGION}:${AWS_ACCOUNT_ID}:instance/${EC2_INSTANCE_ID}"
TOKEN_PARAMETER_ARN="arn:aws:ssm:${AWS_REGION}:${AWS_ACCOUNT_ID}:parameter/aiserver1/deploy/*"

working_directory="$(mktemp -d)"
cleanup() {
  rm -rf "${working_directory}"
}
trap cleanup EXIT

if ! aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "${OIDC_ARN}" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url "https://${OIDC_HOST}" \
    --client-id-list sts.amazonaws.com \
    --tags Key=ManagedBy,Value=aiserver1-cicd >/dev/null
fi

cat >"${working_directory}/github-trust.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Federated": "${OIDC_ARN}"},
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_HOST}:aud": "sts.amazonaws.com",
          "${OIDC_HOST}:sub": "${GITHUB_OIDC_SUBJECT}"
        }
      }
    }
  ]
}
EOF

if aws iam get-role --role-name "${DEPLOY_ROLE_NAME}" >/dev/null 2>&1; then
  aws iam update-assume-role-policy \
    --role-name "${DEPLOY_ROLE_NAME}" \
    --policy-document "file://${working_directory}/github-trust.json"
else
  aws iam create-role \
    --role-name "${DEPLOY_ROLE_NAME}" \
    --description "Deploy aiserver1 from GitHub Actions to EC2 through SSM" \
    --assume-role-policy-document "file://${working_directory}/github-trust.json" \
    --tags Key=ManagedBy,Value=aiserver1-cicd >/dev/null
fi

cat >"${working_directory}/github-permissions.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SendDeploymentCommand",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ssm:${AWS_REGION}::document/AWS-RunShellScript",
        "${INSTANCE_ARN}"
      ]
    },
    {
      "Sid": "ReadDeploymentResult",
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    },
    {
      "Sid": "ManageEphemeralGhcrToken",
      "Effect": "Allow",
      "Action": ["ssm:PutParameter", "ssm:DeleteParameter"],
      "Resource": "${TOKEN_PARAMETER_ARN}"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "${DEPLOY_ROLE_NAME}" \
  --policy-name aiserver1-deploy \
  --policy-document "file://${working_directory}/github-permissions.json"

cat >"${working_directory}/ec2-trust.json" <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

if aws iam get-role --role-name "${EC2_ROLE_NAME}" >/dev/null 2>&1; then
  aws iam update-assume-role-policy \
    --role-name "${EC2_ROLE_NAME}" \
    --policy-document "file://${working_directory}/ec2-trust.json"
else
  aws iam create-role \
    --role-name "${EC2_ROLE_NAME}" \
    --description "Allow the aiserver1 EC2 instance to use SSM and pull GHCR images" \
    --assume-role-policy-document "file://${working_directory}/ec2-trust.json" \
    --tags Key=ManagedBy,Value=aiserver1-cicd >/dev/null
fi

aws iam attach-role-policy \
  --role-name "${EC2_ROLE_NAME}" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

cat >"${working_directory}/ec2-permissions.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadEphemeralGhcrToken",
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "${TOKEN_PARAMETER_ARN}"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "${EC2_ROLE_NAME}" \
  --policy-name aiserver1-read-deploy-token \
  --policy-document "file://${working_directory}/ec2-permissions.json"

if ! aws iam get-instance-profile \
  --instance-profile-name "${INSTANCE_PROFILE_NAME}" >/dev/null 2>&1; then
  aws iam create-instance-profile \
    --instance-profile-name "${INSTANCE_PROFILE_NAME}" \
    --tags Key=ManagedBy,Value=aiserver1-cicd >/dev/null
fi

profile_role="$(
  aws iam get-instance-profile \
    --instance-profile-name "${INSTANCE_PROFILE_NAME}" \
    --query 'InstanceProfile.Roles[0].RoleName' \
    --output text
)"
if [[ "${profile_role}" == "None" ]]; then
  aws iam add-role-to-instance-profile \
    --instance-profile-name "${INSTANCE_PROFILE_NAME}" \
    --role-name "${EC2_ROLE_NAME}"
elif [[ "${profile_role}" != "${EC2_ROLE_NAME}" ]]; then
  echo "Instance profile already contains an unexpected role: ${profile_role}" >&2
  exit 1
fi

association_id="$(
  aws ec2 describe-iam-instance-profile-associations \
    --region "${AWS_REGION}" \
    --filters "Name=instance-id,Values=${EC2_INSTANCE_ID}" \
    --query "IamInstanceProfileAssociations[?State!='disassociated'][0].AssociationId" \
    --output text
)"
associated_profile_arn="$(
  aws ec2 describe-iam-instance-profile-associations \
    --region "${AWS_REGION}" \
    --filters "Name=instance-id,Values=${EC2_INSTANCE_ID}" \
    --query "IamInstanceProfileAssociations[?State!='disassociated'][0].IamInstanceProfile.Arn" \
    --output text
)"
expected_profile_arn="arn:aws:iam::${AWS_ACCOUNT_ID}:instance-profile/${INSTANCE_PROFILE_NAME}"

if [[ -z "${association_id}" || "${association_id}" == "None" || "${association_id}" == "null" ]]; then
  aws ec2 associate-iam-instance-profile \
    --region "${AWS_REGION}" \
    --instance-id "${EC2_INSTANCE_ID}" \
    --iam-instance-profile Name="${INSTANCE_PROFILE_NAME}" >/dev/null
elif [[ "${associated_profile_arn}" != "${expected_profile_arn}" ]]; then
  aws ec2 replace-iam-instance-profile-association \
    --region "${AWS_REGION}" \
    --association-id "${association_id}" \
    --iam-instance-profile Name="${INSTANCE_PROFILE_NAME}" >/dev/null
fi

if ! ingress_result="$(
  aws ec2 authorize-security-group-ingress \
    --region "${AWS_REGION}" \
    --group-id "${SECURITY_GROUP_ID}" \
    --ip-permissions \
      "IpProtocol=tcp,FromPort=${APP_PORT},ToPort=${APP_PORT},IpRanges=[{CidrIp=0.0.0.0/0,Description=aiserver1}]" \
    --no-cli-pager 2>&1
)"; then
  if [[ "${ingress_result}" != *"InvalidPermission.Duplicate"* ]]; then
    echo "${ingress_result}" >&2
    exit 1
  fi
fi

aws iam get-role \
  --role-name "${DEPLOY_ROLE_NAME}" \
  --query 'Role.{RoleName:RoleName,Arn:Arn}' \
  --output table
aws ec2 describe-iam-instance-profile-associations \
  --region "${AWS_REGION}" \
  --filters "Name=instance-id,Values=${EC2_INSTANCE_ID}" \
  --query 'IamInstanceProfileAssociations[].{InstanceId:InstanceId,State:State,Profile:IamInstanceProfile.Arn}' \
  --output table
aws ec2 describe-security-groups \
  --region "${AWS_REGION}" \
  --group-ids "${SECURITY_GROUP_ID}" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`${APP_PORT}\`].{From:FromPort,To:ToPort,CIDR:IpRanges[].CidrIp}" \
  --output table
