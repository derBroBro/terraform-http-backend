import random
import string
import logging
import boto3
import botocore
from datetime import datetime
from jinja2 import FileSystemLoader, Environment
import os
import json
import urllib.parse
import check_security
import check_costs


logger = logging.getLogger()

BUCKET = os.environ.get("S3_BUCKET")
DEFAULT_STATE = "templates/default.tfstate.template"
VERSION = "0.1.0"


def randomString(stringLength=10):
    """Generate a random string of fixed length """
    letters = string.ascii_lowercase + string.ascii_uppercase + string.digits
    return "".join(random.choice(letters) for i in range(stringLength))


def create_response(body, code=200, contenttype="application/json"):
    logger.info(f"{code} --> {body}")
    return {"statusCode": code, "body": body, "headers": {"Content-Type": contenttype,}}


def redirect(url):
    logger.info(f"Redirect to {url}")
    return {"statusCode": 301, "body": "body", "headers": {"Location": url,}}


def write_key(filename, data):
    logger.info(f"write file {filename} to {BUCKET}")
    s3_client = boto3.resource("s3")
    s3_obj = s3_client.Object(BUCKET, filename)
    s3_obj.put(Body=data)


def read_key_or_default(filename, default_value=None):
    logger.info(f"read file {filename} to {BUCKET}")
    s3_client = boto3.resource("s3")
    s3_obj = s3_client.Object(BUCKET, filename)
    try:
        data = s3_obj.get()["Body"].read()
        return data
    # make nicer and chat execption
    except botocore.exceptions.ClientError as e:
        logger.warn(f"No file yet, send default value ({default_value})")
        if not default_value == None:
            default_value = default_value.encode("UTF8")
        return default_value


def read_file(filename):
    with open(filename, "r") as file:
        return file.read()


def get_tf_res(tf_state, is_raw=False):
    if is_raw:
        try:
            tf_state = json.loads(tf_state)
        except:
            return []
    result = []

    resources = []
    if "resources" in resources:
        tf_state["resources"]
        
    for res in resources:
        if res["mode"] == "managed":
            name = res["name"]

            if "module" in res:
                mod = res["module"]
                res["id"] = f"{mod}.{name}"
            else:
                res["id"] = name

            # get costs
            res["costs"] = check_costs.run(res)
            res["security"] = check_security.run(res)

            result.append(res)

    return result


def get_tf_metadata(tf_state, is_raw=False):
    if is_raw:
        try:
            tf_state = json.loads(tf_state)
        except:
            return {"version": -1, "terraform_version": "invalid", "serial": -1}

    version = 0
    terraform_version = 0
    serial = 0

    if "version" in tf_state:
        version = tf_state["version"]
    if "terraform_version" in tf_state:
        terraform_version = tf_state["terraform_version"]
    if "serial" in tf_state:
        serial = tf_state["serial"]

    return {
        "version": version,
        "terraform_version": terraform_version,
        "serial": serial,
    }


def get_post_parameter(event):
    body_vars = {}
    if not "body" in event:
        return body_vars

    body = urllib.parse.unquote(event["body"])
    for line in body.split("&"):
        line_data = line.split("=")
        if len(line_data) == 2:
            body_vars[line_data[0]] = line_data[1]
    return body_vars


def new_project(name, owner):
    project_id = randomString(48)
    token = randomString(24)
    config = json.dumps({"name": name, "owner": owner, "token": token})
    state = read_file(DEFAULT_STATE)

    logger.info(f"Create project {name} with id {project_id}")

    write_key(f"{project_id}/config.json", config)
    write_key(f"{project_id}/terraform.tfstate", state)

    return project_id


def get_config(project_id):
    raw_data = read_key_or_default(
        f"{project_id}/config.json",
        '{"name":"invalid","token":"invalid","owner":"invalid"}',
    )
    return json.loads(raw_data)


def render_template(template_file, **kwargs):
    templateLoader = FileSystemLoader(searchpath=f"./templates")
    templateEnv = Environment(loader=templateLoader)
    template = templateEnv.get_template(template_file)
    result = template.render(kwargs, version=VERSION)
    return result


def gen_report(project_id):
    report = {}
    report["config"] = get_config(project_id)
    state = json.loads(read_key_or_default(f"{project_id}/terraform.tfstate", "{}"))
    report["metadata"] = get_tf_metadata(state)
    report["resources"] = get_tf_res(state)
    report["last_update"] = datetime.now().isoformat()
    report["state"] = -1

    # get the worst state
    for res in report["resources"]:
        if res["security"]["state"] > report["state"]:
            report["state"] = res["security"]["state"]

    write_key(f"{project_id}/report.json", json.dumps(report, indent=4))
    return report

def get_report(project_id):
    report_path = f"{project_id}/report.json"
    report = json.loads(read_key_or_default(report_path, "{}"))
    return report

def get_reports():
    result = []
    s3_client = boto3.client("s3")
    projects = s3_client.list_objects_v2(Bucket=BUCKET)
    for project in projects["Contents"]:
        key_path = project["Key"]
        if(key_path.endswith("/config.json")):
            project_id = key_path.replace("/config.json","")
            project_report = get_report(project_id)
            if not "config" in project_report:
                project_report = gen_report(project_id)

            project_report["id"] = project_id
            result.append(project_report)
            logger.info("add report")
    logger.info(result)
    return result


def send_message(target_arn, project_id, message, subject=""):
    logger.info(f"Send {subject}/{message} to {target_arn}")
    sns_client = boto3.client("sns")
    payload = {
        "default": message,
        "email": message,
        "sqs": message,
        "lambda": project_id,
        "http": project_id,
        "https": project_id,
    }
    result = sns_client.publish(
        TopicArn=target_arn,
        Message=json.dumps(payload),
        Subject=subject,
        MessageStructure="json",
    )
    logger.info(result)


def gen_test_project():
    project_id = new_project(name="test", owner="test@test.de")
    tf_raw_state = read_file("test_data/terraform.teststate")
    write_key(f"{project_id}/terraform.tfstate", tf_raw_state)
    return project_id
