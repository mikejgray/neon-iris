# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2025 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os
import unittest

from unittest.mock import Mock, patch
from neon_utils.socket_utils import dict_to_b64
from ovos_bus_client.message import Message

from neon_iris.mq_connector import IrisConnector
from neon_iris.client import NeonAIClient

_test_config = {
    "MQ": {
        "server": "mq.neonaialpha.com",
        "port": 25672,
        "users": {
            "mq_handler": {
                "user": "neon_api_utils",
                "password": "Klatchat2021"
            }
        }
    }
}


class TestClient(unittest.TestCase):
    def test_client_create(self):
        client = NeonAIClient(_test_config)
        self.assertIsInstance(client.uid, str)
        self.assertEqual(client._config, _test_config)
        self.assertEqual(client._connection.config, _test_config["MQ"])
        self.assertTrue(os.path.isdir(client.audio_cache_dir))
        self.assertIsInstance(client.client_name, str)
        self.assertIsInstance(client.connection, IrisConnector)
        self.assertEqual(client.connection.vhost, "/neon_chat_api")
        client.shutdown()


class _NotificationClient(NeonAIClient):
    """
    Minimal subclass that records notifications instead of logging them
    """
    def __init__(self):
        # Bypass NeonAIClient.__init__ to avoid opening an MQ connection
        self.received = list()

    def handle_notification(self, message: Message):
        self.received.append(message)


def _deliver(client: NeonAIClient, msg_type: str, data: dict) -> Message:
    """
    Push a serialized MQ message through `handle_neon_response` the same way
    the consumer thread would, returning the Message it was deserialized to
    """
    body = dict_to_b64({"msg_type": msg_type, "data": data, "context": {}})
    channel = Mock()
    method = Mock()
    client.handle_neon_response(channel, method, None, body)
    channel.basic_ack.assert_called_once_with(
        delivery_tag=method.delivery_tag)
    return Message(msg_type, data, {})


class TestNotificationDispatch(unittest.TestCase):
    notification_types = ("ovos.notification.api.notify",
                          "ovos.notification.api.dismiss",
                          "ovos.notification.api.snoozed")

    def setUp(self):
        # Bypass NeonAIClient.__init__ to avoid opening an MQ connection
        self.client = NeonAIClient.__new__(NeonAIClient)

    def test_notification_types_dispatch_to_handle_notification(self):
        for msg_type in self.notification_types:
            with self.subTest(msg_type=msg_type):
                with patch.object(self.client, "handle_notification") as \
                        handler, patch.object(self.client,
                                              "handle_api_response") as api:
                    _deliver(self.client, msg_type, {"notification_id": "n1"})
                    handler.assert_called_once()
                    dispatched = handler.call_args.args[0]
                    self.assertIsInstance(dispatched, Message)
                    self.assertEqual(dispatched.msg_type, msg_type)
                    self.assertEqual(dispatched.data["notification_id"], "n1")
                    api.assert_not_called()

    def test_subclass_override_receives_message(self):
        client = _NotificationClient()
        notification = {"notification_id": "n2", "text": "hello"}
        _deliver(client, "ovos.notification.api.notify",
                 {"notification": notification})
        self.assertEqual(len(client.received), 1)
        self.assertEqual(client.received[0].msg_type,
                         "ovos.notification.api.notify")
        self.assertEqual(client.received[0].data["notification"],
                         notification)

    def test_notification_responses_still_route_to_api_response(self):
        response_types = ("ovos.notification.api.sync.request.response",
                          "ovos.notification.api.set.response",
                          "ovos.notification.api.remove.response",
                          "ovos.notification.api.snooze.response")
        for msg_type in response_types:
            with self.subTest(msg_type=msg_type):
                with patch.object(self.client, "handle_api_response") as \
                        api, patch.object(self.client,
                                          "handle_notification") as handler:
                    _deliver(self.client, msg_type, {"notification_id": "n3"})
                    api.assert_called_once()
                    self.assertEqual(api.call_args.args[0].msg_type, msg_type)
                    handler.assert_not_called()

    def test_existing_dispatch_unaffected(self):
        with patch.object(self.client, "handle_alert") as alert, \
                patch.object(self.client, "handle_notification") as handler:
            _deliver(self.client, "neon.alert_expired", {"alert_name": "a"})
            alert.assert_called_once()
            handler.assert_not_called()

    def test_default_handle_notification_logs(self):
        with patch("neon_iris.client.LOG") as log:
            _deliver(self.client, "ovos.notification.api.dismiss",
                     {"notification_id": "n4"})
            logged = " ".join(str(a) for c in log.info.call_args_list
                              for a in c.args)
            self.assertIn("ovos.notification.api.dismiss", logged)
            self.assertIn("n4", logged)

        with patch("neon_iris.client.LOG") as log:
            _deliver(self.client, "ovos.notification.api.notify",
                     {"notification": {"notification_id": "n5"}})
            logged = " ".join(str(a) for c in log.info.call_args_list
                              for a in c.args)
            self.assertIn("n5", logged)

