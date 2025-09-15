import json
from typing import Any, Dict, Optional, Union

try:
    from jupyter_server.services.kernels.websocket import (
        KernelWebsocketHandler as WebsocketHandler,
    )
    from jupyter_server.services.kernels.connection.base import (
        deserialize_binary_message,
        deserialize_msg_from_ws_v1,
    )
except ImportError:
    from jupyter_server.services.kernels.handlers import (
        ZMQChannelsHandler as WebsocketHandler,
    )


def read_header_from_binary_message(ws_msg: bytes) -> Optional[Dict]:
    """Read message header using the v1 protocol."""

    offset_number = int.from_bytes(ws_msg[:8], "little")
    offsets = [
        int.from_bytes(ws_msg[8 * (i + 1) : 8 * (i + 2)], "little")
        for i in range(offset_number)
    ]
    try:
        header = ws_msg[offsets[1] : offsets[2]].decode("utf-8")
        return json.loads(header)
    except Exception:
        return


class VoilaKernelWebsocketHandler(WebsocketHandler):

    _execution_data = {}

    def on_message(self, ws_msg):
        connection = self.connection
        subprotocol = connection.subprotocol
        if not connection.channels:
            # already closed, ignore the message
            connection.log.debug("Received message on closed websocket %r", ws_msg)
            return

        if subprotocol == "v1.kernel.websocket.jupyter.org":
            channel, msg_list = deserialize_msg_from_ws_v1(ws_msg)
            msg = {"header": None, "content": None}
        else:
            if isinstance(ws_msg, bytes):  # type:ignore[unreachable]
                msg = deserialize_binary_message(ws_msg)  # type:ignore[unreachable]
            else:
                msg = json.loads(ws_msg)
            msg_list = []
            channel = msg.pop("channel", None)

        if channel is None:
            connection.log.warning("No channel specified, assuming shell: %s", msg)
            channel = "shell"
        if channel not in connection.channels:
            connection.log.warning("No such channel: %r", channel)
            return
        am = connection.multi_kernel_manager.allowed_message_types
        ignore_msg = False
        msg_header = connection.get_part("header", msg["header"], msg_list)
        msg_content = connection.get_part("content", msg["content"], msg_list)
        if msg_header["msg_type"] == "execute_request":
            execution_data = self._execution_data.get(self.kernel_id, None)
            cells = execution_data["cells"]
            code = msg_content.get("code")
            try:
                cell_idx = int(code)
                cell = cells[cell_idx]
                if cell["cell_type"] != "code":
                    cell["source"] = ""

                if subprotocol == "v1.kernel.websocket.jupyter.org":
                    msg_content["code"] = cell["source"]
                    msg_list[3] = connection.session.pack(msg_content)
                else:
                    msg["content"]["code"] = cell["source"]

            except Exception:
                connection.log.warning("Unsupported code cell %s" % code)

        if am:
            msg["header"] = connection.get_part("header", msg["header"], msg_list)
            assert msg["header"] is not None
            if msg["header"]["msg_type"] not in am:  # type:ignore[unreachable]
                connection.log.warning(
                    'Received message of type "%s", which is not allowed. Ignoring.'
                    % msg["header"]["msg_type"]
                )
                ignore_msg = True
        if not ignore_msg:
            stream = connection.channels[channel]
            if subprotocol == "v1.kernel.websocket.jupyter.org":
                connection.session.send_raw(stream, msg_list)
            else:
                connection.session.send(stream, msg)

    def write_message(
        self, message: Union[bytes, Dict[str, Any]], binary: bool = False
    ):

        if isinstance(message, bytes):
            header = read_header_from_binary_message(message)
        elif isinstance(message, dict):
            header = message.get("header", None)
        else:
            header = None

        if header and header.get("msg_type", None) == "execute_input":
            return  # Ignore execute_input message

        return super().write_message(message, binary)
