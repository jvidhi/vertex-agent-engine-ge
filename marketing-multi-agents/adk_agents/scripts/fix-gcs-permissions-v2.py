from google.cloud import storage

def grant_object_admin(bucket_name, service_account, project_id):
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)

    policy.bindings.append({
        "role": "roles/storage.objectAdmin",
        "members": {f"serviceAccount:{service_account}"}
    })

    bucket.set_iam_policy(policy)
    print(f"Granted roles/storage.objectAdmin to {service_account} on bucket {bucket_name}")

if __name__ == "__main__":
    PROJECT = "n26-genkey-fabricofuint"
    BUCKET = "fabricofuint_organic_living_artifacts"
    SA = "service-506956666883@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
    try:
        grant_object_admin(BUCKET, SA, PROJECT)
    except Exception as e:
        print(f"Failed to update IAM policy: {e}")
