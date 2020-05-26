import decimal
import importlib
import json
from datetime import datetime
from unittest import mock

import boto3
import dateparser
from django.conf import settings
from django.db import connection


def json_dump(obj):
    if isinstance(obj, datetime):
        return {"__type__": "datetime", "value": obj.isoformat()}
    elif isinstance(obj, decimal.Decimal):
        return {"__type__": "decimal", "value": str(obj)}
    elif isinstance(obj, set):
        return {"__type__": "set", "value": list(obj)}
    else:
        assert False, type(obj)


def json_load(obj):
    if "__type__" in obj:
        if obj["__type__"] == "datetime":
            return dateparser.parse(obj["value"])
        elif obj["__type__"] == "decimal":
            return decimal.Decimal(obj["value"])
        elif obj["__type__"] == "set":
            return set(obj["value"])
        else:
            assert False
    else:
        return obj


class _TaskServiceBase:
    def run_task(self, body):
        data = json.loads(body, object_hook=json_load)
        self.run(data["module"], data["method"], data["args"], data["kwargs"])

    def run(self, module_name, method_name, args, kwargs):
        """ run a task, called by the view that receives them from the queue """
        kwargs["_immediate"] = True
        module = importlib.import_module(module_name)
        method = getattr(module, method_name)
        assert method._is_task
        method(*args, **kwargs)

    def enqueue(self, module_name, method_name, args, kwargs):
        body = json.dumps(
            {
                "module": module_name,
                "method": method_name,
                "args": args,
                "kwargs": kwargs,
            },
            default=json_dump,
        )
        self._enqueue(body)


class FakeTaskService(_TaskServiceBase):
    def __init__(self):
        self.clear()

    def _enqueue(self, body):
        self.queue.append(body)

    def clear(self):
        """ wipe the test queue """
        self.queue = []

    def run_all(self):
        """ run everything in the test queue """
        # clear on_commit stuff
        if connection.in_atomic_block:
            while connection.run_on_commit:
                sids, func = connection.run_on_commit.pop(0)
                func()

        while self.queue:
            self.run_task(self.queue.pop(0))

    def run_task(self, body):
        with mock.patch("django.conf.settings.BEANSTALK_WORKER", True):
            return super().run_task(body)


class TaskService(_TaskServiceBase):
    def _enqueue(self, body):
        sqs = boto3.client("sqs", region_name=settings.BEANSTALK_SQS_REGION)
        sqs.send_message(
            QueueUrl=settings.BEANSTALK_SQS_URL, MessageAttributes={}, MessageBody=body
        )
