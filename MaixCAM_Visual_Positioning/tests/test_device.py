"""A reachable HTTP agent with a frozen camera must not look like live video."""
import base64
import json
import io
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"host"))
from device import Device,ssh_socket


def wait_for(predicate,timeout=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return True
        time.sleep(.02)
    return False


class FrozenResponse:
    headers={"X-Frame-Meta":base64.b64encode(json.dumps({
        "seq":1,"device_id":"test-camera","mode":"off"
    }).encode()).decode()}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return b"\xff\xd8unchanging-jpeg-test-only"


class DeviceTests(unittest.TestCase):
    def test_name_with_both_address_families_prefers_ipv4(self):
        ipv6=(socket.AF_INET6,socket.SOCK_STREAM,6,'',('fe80::1',22,0,12))
        ipv4=(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.191.17.1',22))
        sock=Mock()
        with patch('device.socket.getaddrinfo',return_value=[ipv6,ipv4]),patch('device.socket.socket',return_value=sock) as create:
            self.assertIs(ssh_socket('camera-test.local'),sock)
        create.assert_called_once_with(*ipv4[:3]);sock.connect.assert_called_once_with(ipv4[4])

    def test_ipv6_zone_is_uri_escaped_without_control_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Device(Path(__file__).resolve().parents[1],temp)
            d.host='fe80::1%12';d.opener=Mock()
            d.request('/status')
            request=d.opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'http://[fe80::1%2512]:18765/status')
            self.assertEqual(request.host,'[fe80::1%12]:18765')

    def test_ssh_ipv6_scope_is_preserved_in_pinned_peer(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Device(Path(__file__).resolve().parents[1],temp)
            ssh=Mock();ssh.get_transport.return_value.getpeername.return_value=('fe80::1',22,0,12)
            d.request=Mock(return_value=io.BytesIO(b'{"ready":true}'));d._poll=Mock()
            with patch('device.ssh_connect',return_value=ssh),patch('device.remote',return_value='maixcam_pro'):
                d.connect('fe80::1%12',deploy=False)
                self.assertTrue(wait_for(lambda:not d.status()['busy']))
            self.assertEqual(d.host,'fe80::1%12')
            self.assertEqual(json.loads(d.config_path.read_text())['host'],'fe80::1%12')

    def test_slow_name_resolution_has_its_own_timeout(self):
        release=threading.Event()
        def lookup(*args,**kwargs):release.wait(2);return []
        with patch('device.socket.getaddrinfo',side_effect=lookup):
            try:
                start=time.monotonic()
                with self.assertRaisesRegex(TimeoutError,"名称解析超时"):
                    ssh_socket('camera-test.invalid',lookup_timeout=.05)
                self.assertLess(time.monotonic()-start,.5)
            finally:release.set()

    def test_cancelled_reader_cannot_overwrite_new_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Device(Path(__file__).resolve().parents[1],temp)
            entered=threading.Event();release=threading.Event();stop=threading.Event()
            def request(*args,**kwargs):
                entered.set();release.wait(2)
                raise OSError("old request finished after reconnect")
            d.request=request
            worker=threading.Thread(target=d._poll,args=(stop,),daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1));stop.set()
                with d.lock:d.state.update(connected=True,message="new connection")
                release.set();worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertTrue(d.state['connected'])
                self.assertEqual(d.state['message'],"new connection")
            finally:stop.set();release.set();worker.join(2)

    def test_repeated_http_jpeg_does_not_refresh_frame_age(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Device(Path(__file__).resolve().parents[1],temp)
            clock=[100.]
            stalled=threading.Event()
            def request(*args,**kwargs):
                clock[0]+=1
                if clock[0]>=106:stalled.set()
                return FrozenResponse()
            d.request=request
            stop=threading.Event()
            with patch("device.time.time",side_effect=lambda:clock[0]):
                worker=threading.Thread(target=d._poll,args=(stop,),daemon=True)
                worker.start()
                try:
                    self.assertTrue(stalled.wait(4),"polling did not reach frozen-frame condition")
                    self.assertFalse(d.status()["connected"])
                    with self.assertRaisesRegex(ValueError,"画面已中断"):
                        d.snapshot()
                    self.assertIn("帧序号停止更新",d.status()["message"])
                finally:
                    stop.set();worker.join(2)
                    self.assertFalse(worker.is_alive())


class AgentHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.headers.get('Authorization')!='Bearer transport-test-only':
            self.send_error(401);return
        if not self.server.available.is_set():
            self.send_error(503);return
        self.server.sequence+=1
        if self.path=='/status':body=json.dumps({'ready':True}).encode();headers={}
        else:
            body=b'\xff\xd8transport-test-only\xff\xd9'
            meta={'seq':self.server.sequence,'device_id':'transport-test-only','mode':'off'}
            headers={'X-Frame-Meta':base64.b64encode(json.dumps(meta).encode()).decode()}
        self.send_response(200);self.send_header('Content-Length',str(len(body)))
        for k,v in headers.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)


class DeviceTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.agent=ThreadingHTTPServer(('127.0.0.1',0),AgentHandler)
        self.agent.daemon_threads=True;self.agent.sequence=0
        self.agent.available=threading.Event();self.agent.available.set()
        threading.Thread(target=self.agent.serve_forever,daemon=True).start()
        self.port=patch('device.AGENT_PORT',self.agent.server_port);self.port.start()
        self.d=Device(Path(__file__).resolve().parents[1],self.temp.name)
        self.d.token='transport-test-only';self.d.host='127.0.0.1'

    def tearDown(self):
        self.d.stop.set()
        if self.d.worker:self.d.worker.join(3)
        self.agent.shutdown();self.agent.server_close();self.port.stop();self.temp.cleanup()

    def start_reader(self):
        self.d.worker=threading.Thread(target=self.d._poll,args=(self.d.stop,),daemon=True)
        self.d.worker.start()

    def test_hostname_ssh_connection_pins_numeric_peer_for_video(self):
        ssh=Mock();ssh.get_transport.return_value.getpeername.return_value=('127.0.0.1',22)
        with patch('device.ssh_connect',return_value=ssh) as connect,patch('device.remote',return_value='maixcam_pro'):
            self.d.connect('camera-name-must-not-resolve.invalid',deploy=False)
            self.assertTrue(wait_for(lambda:self.d.status()['connected']))
            connect.assert_called_once()
        self.assertEqual(self.d.host,'127.0.0.1')
        self.assertEqual(json.loads(self.d.config_path.read_text())['host'],'127.0.0.1')
        self.assertTrue(self.d.snapshot()[0].startswith(b'\xff\xd8'))

    def test_slow_local_name_falls_back_to_same_named_usb_camera(self):
        ssh=Mock();ssh.get_transport.return_value.getpeername.return_value=('127.0.0.1',22)
        with patch('device.ssh_connect',side_effect=[TimeoutError('lookup stalled'),ssh]) as connect,patch('device.remote',return_value='maixcam_pro\ncamera-test\n'):
            self.d.connect('camera-test.local',deploy=False)
            self.assertTrue(wait_for(lambda:self.d.status()['connected']))
            self.assertEqual(connect.call_count,2)
        self.assertEqual(self.d.host,'127.0.0.1')

    def test_temporary_transport_failure_recovers_without_ssh_or_deploy(self):
        with patch('device.ssh_connect') as ssh:
            self.start_reader();self.assertTrue(wait_for(lambda:self.d.status()['connected']))
            first=self.d.snapshot()[1]['seq'];self.agent.available.clear()
            self.assertTrue(wait_for(lambda:not self.d.status()['connected']))
            self.assertTrue(self.d.status()['reconnecting'])
            self.agent.available.set()
            self.assertTrue(wait_for(lambda:self.d.status()['connected'] and self.d.meta['seq']>first))
            self.assertFalse(self.d.status()['reconnecting']);ssh.assert_not_called()

    def test_failed_usb_interface_uses_other_authenticated_interface(self):
        self.d.host='10.191.17.1';request=self.d.request;attempts=[]
        def route(path,body=None,timeout=5,host=None):
            attempts.append(host)
            if host=='10.191.17.1':raise urllib.error.URLError('simulated USB interface outage')
            return request(path,body,timeout,host='127.0.0.1')
        self.d.request=route;self.start_reader()
        self.assertTrue(wait_for(lambda:self.d.status()['connected']))
        self.assertEqual(self.d.status()['host'],'10.191.18.1')
        self.assertIn('10.191.17.1',attempts);self.assertIn('10.191.18.1',attempts)


if __name__=="__main__":unittest.main()
