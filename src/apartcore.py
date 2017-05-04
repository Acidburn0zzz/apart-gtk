import uuid
import yaml
import subprocess
import sys
import os
import zmq
from threading import Thread
from typing import *
from util import default_datetime_to_utc

# True: print out messages <--> core
LOG_MESSAGES = False


class MessageListener:
    def __init__(self, on_message: Callable[[Dict], None],
                 message_predicate: Callable[[Dict], bool] = lambda m: True,
                 listen_to: 'ApartCore' = None,
                 one_time: bool = False):
        self.one_time = one_time
        self.input_on_message = on_message

        self.message_predicate = message_predicate
        self.remove_fn = None
        if listen_to:
            self.listen_to(listen_to)

    def listen_to(self, core: 'ApartCore') -> 'MessageListener':
        self.remove_fn = core.register(self)
        return self

    def stop_listening(self) -> 'MessageListener':
        if self.remove_fn is not None:
            self.remove_fn()
        return self

    def on_message(self, msg: Dict) -> bool:
        """Return False implies stop listening"""
        self.input_on_message(msg)
        return not self.one_time


class ApartCore(Thread):
    def __init__(self, listeners: List[MessageListener] = None):
        """Starts an apart-core command and starts listening for zmq messages on this new thread"""
        Thread.__init__(self, name='apart-core-runner')
        self.ipc_address = 'ipc:///tmp/apart-gtk-{}.ipc'.format(uuid.uuid4())
        self.zmq_context = zmq.Context()
        self.socket = self.zmq_context.socket(zmq.PAIR)
        self.socket.bind(self.ipc_address)
        self.listeners: List[MessageListener] = listeners or []

        if LOG_MESSAGES:
            self.register(MessageListener(lambda msg: print('apart-core ->\n {}'.format(str(msg)))))

        apart_core_cmd = os.environ.get('APART_GTK_CORE_CMD') or 'apart-core'  # TODO confirm default
        try:
            self.process = subprocess.Popen([apart_core_cmd, self.ipc_address])
        except FileNotFoundError:
            sys.stderr.write('apart-core command not found at \'' + apart_core_cmd + '\'')
            self.zmq_context.destroy()
            sys.exit(1)

        self.start()

    def run(self):
        while self.process.returncode is None:
            msg = yaml.load(self.socket.recv_string())
            default_datetime_to_utc(msg)
            to_remove = []
            for listener in self.listeners:
                if listener.message_predicate(msg):
                    if not listener.on_message(msg):
                        to_remove.append(listener)
            for listener in to_remove:
                listener.stop_listening()
            if msg['type'] == 'status' and msg['status'] == 'dying':
                break
        self.zmq_context.destroy()

    def kill(self):
        if not self.zmq_context.closed:
            self.socket.send_string('type: kill-request')
        self.join()

    def send(self, message: str):
        if not self.zmq_context.closed:
            self.socket.send_string(message)
            if LOG_MESSAGES:
                print('apart-core <-\n----\n{}\n----'.format(message))

    def register(self, message_listener: MessageListener) -> Callable[[], None]:
        """:return: remove function"""
        self.listeners.append(message_listener)
        return lambda: self.listeners.remove(message_listener)
