#!/usr/bin/env python3
"""Standalone S3 deploy check for the Litter-Robot dashboards.

Use this to find out *exactly* why a dashboard is or isn't reaching S3. It uses
the same bucket, region, credential chain, and object keys as litter_robot_v1.py,
and uploads each of the three dashboards independently, printing the precise AWS
error for any that fail (e.g. AccessDenied), plus the AWS identity in use.

    python s3_deploy_check.py            # try to upload all three, report results
    python s3_deploy_check.py --list     # also list what is currently in the bucket

If a file fails with AccessDenied, the credentials/policy in use are not allowed
to PutObject for that key. Fix: allow s3:PutObject on the whole bucket prefix,
e.g. Resource "arn:aws:s3:::litterrobot.sunit.dev/*" (not a single object key).
"""
import argparse
import sys
from pathlib import Path

# Keep these in sync with litter_robot_v1.py
S3_BUCKET = "litterrobot.sunit.dev"
AWS_REGION = "us-east-1"
FILES = [
    ("litter_robot_dashboard.html", "litter_robot_dashboard.html"),
    ("cat_health_dashboard.html",   "cat_health_dashboard.html"),
    ("batch_run_dashboard.html",    "batch_run_dashboard.html"),
]


def dashboards_dir() -> Path:
    """Find the folder that holds the generated dashboard HTML files."""
    here = Path(__file__).resolve().parent
    for d in (here.parent / "dashboards", here, Path.cwd()):
        if (d / "litter_robot_dashboard.html").exists():
            return d
    return here.parent / "dashboards"


def main() -> None:
    ap = argparse.ArgumentParser(description="Check/perform S3 upload of all 3 dashboards.")
    ap.add_argument("--list", action="store_true", help="Also list current bucket objects.")
    args = ap.parse_args()

    try:
        import boto3
    except ImportError:
        print("boto3 not installed. Install it with:  pip install boto3")
        sys.exit(1)

    ddir = dashboards_dir()
    print(f"Dashboards dir : {ddir}")
    print(f"Target bucket  : s3://{S3_BUCKET}   (region {AWS_REGION})")

    s3 = boto3.client("s3", region_name=AWS_REGION)

    # Print which AWS identity we're using — invaluable for debugging permissions.
    try:
        ident = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()
        print(f"AWS identity   : {ident.get('Arn')}")
    except Exception as exc:
        print(f"AWS identity   : (could not determine: {type(exc).__name__}: {exc})")
    print()

    ok = 0
    for fname, key in FILES:
        path = ddir / fname
        if not path.exists():
            print(f"  SKIP  {key}: file not found locally at {path}")
            continue
        try:
            s3.upload_file(
                str(path), S3_BUCKET, key,
                ExtraArgs={"ContentType": "text/html; charset=utf-8",
                           "CacheControl": "no-cache"},
            )
            print(f"  OK    {key}")
            ok += 1
        except Exception as exc:
            print(f"  FAIL  {key}: {type(exc).__name__}: {exc}")

    print(f"\n{ok}/{len(FILES)} uploaded successfully.")

    if args.list:
        print("\nObjects currently in the bucket:")
        try:
            resp = s3.list_objects_v2(Bucket=S3_BUCKET)
            contents = resp.get("Contents", [])
            if not contents:
                print("  (none)")
            for o in sorted(contents, key=lambda x: x["Key"]):
                print(f"  {o['Key']}  ({o['Size']:,} bytes)")
        except Exception as exc:
            print(f"  could not list bucket: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
