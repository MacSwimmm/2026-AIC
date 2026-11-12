import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"host"))
from server import Application,Handler
from test_geometry import fixture


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Application(self.temp.name)
        self.app.device.frame=b'\xff\xd8fake-test-only\xff\xd9'
        self.app.device.meta={'seq':1,'received_at':time.time(),'captured_at':time.time(),'device_id':'test-camera','geometry_id':'640x480-rgb-v1','width':640,'height':480,'detections':[]}
        self.app.device.state['connected']=True
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.http.daemon_threads=True;self.http.app=self.app
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.temp.cleanup()

    def request(self,path,data=None,headers=None):
        req=urllib.request.Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with self.op.open(req) as r:return r.status,r.read(),dict(r.headers)
        except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)

    def calibrate(self):
        code,raw,_=self.request('/api/calibration',fixture());self.assertEqual(code,200,raw)
        return json.loads(raw)

    def test_coordinate_requires_current_calibration(self):
        self.assertEqual(self.request('/api/point',{'pixel':[320,167]})[0],400)
        self.calibrate();code,b,_=self.request('/api/point',{'pixel':[320,167.64705882352942]})
        self.assertEqual(code,200);self.assertAlmostEqual(json.loads(b)['y'],2.5,places=6)

    def test_stale_and_wrong_device_disable_outputs(self):
        self.calibrate();self.app.device.meta['received_at']=time.time()-10
        self.assertEqual(self.request('/api/point',{'pixel':[320,167]})[0],400)
        self.assertFalse(json.loads(self.request('/api/status')[1])['calibration_compatible'])
        self.app.device.meta.update(received_at=time.time(),device_id='another-camera')
        self.assertEqual(self.request('/api/point',{'pixel':[320,167]})[0],400)

    def test_wrong_resolution_disables_outputs(self):
        self.calibrate();self.app.device.meta['width']=320
        self.assertEqual(self.request('/api/point',{'pixel':[100,100]})[0],400)

    def test_outside_click_and_nonfinite_input_rejected(self):
        self.calibrate()
        for p in [[0,0],[-1,100],[float('nan'),100]]:
            self.assertEqual(self.request('/api/point',{'pixel':p})[0],400)

    def test_targets_export_and_new_calibration_clears_old_targets(self):
        self.calibrate();code,b,_=self.request('/api/targets',{'pixel':[320,167.6470588],'name':'目标一'})
        self.assertEqual(code,200,b)
        exported=json.loads(self.request('/api/targets/export')[1]);self.assertEqual(exported['units'],'m');self.assertFalse(exported['navigation_sent'])
        self.assertAlmostEqual(exported['targets'][0]['x'],2.5,places=6)
        self.calibrate();self.assertEqual(self.app.targets,[])

    def test_import_recomputes_matrix_and_checks_identity(self):
        data=self.calibrate();data['homography']=[[1,0,999],[0,1,999],[0,0,1]]
        self.assertEqual(self.request('/api/calibration/import',data)[0],200)
        self.assertAlmostEqual(self.app.cal.to_world([320,167.64705882352942])['x'],2.5,places=6)
        data['device_id']='wrong';self.assertEqual(self.request('/api/calibration/import',data)[0],400)

    def test_persistence_and_clear(self):
        self.calibrate();new=Application(self.temp.name);self.assertIsNotNone(new.cal)
        self.request('/api/calibration/clear',{});new=Application(self.temp.name);self.assertIsNone(new.cal)

    def test_frame_and_metadata_share_sequence_and_detections(self):
        self.calibrate();self.app.device.meta['detections']=[{'label':'person','anchor':[320,167.64705882352942],'bbox':[300,100,40,68],'score':.8}]
        code,b,h=self.request('/api/frame.jpg');self.assertEqual(code,200)
        meta=json.loads(base64.b64decode(h['X-Frame-Meta']))
        self.assertEqual(meta['seq'],1);self.assertAlmostEqual(meta['detections'][0]['world']['x'],2.5,places=6)
        self.assertEqual(b,self.app.device.frame)

    def test_cross_origin_write_and_path_escape_rejected(self):
        self.assertEqual(self.request('/api/calibration/clear',{},headers={'Origin':'http://example.com'})[0],403)
        self.assertEqual(self.request('/../server.py')[0],404)
        self.assertEqual(self.request('/data/connection.json')[0],404)

    def test_validation_has_no_corner_fit_accuracy_claim(self):
        data=self.calibrate();self.assertEqual(data['validation']['count'],0);self.assertIsNone(data['validation']['rmse_m'])
        self.assertEqual(self.request('/api/check',{'pixel':[60,420],'actual':[0,0]})[0],400)

    def test_status_responds_while_device_mode_request_is_waiting(self):
        entered=threading.Event();release=threading.Event();result=[]
        def set_mode(mode):
            entered.set();release.wait(3);return {'requested_mode':mode}
        with patch.object(self.app.device,'set_mode',side_effect=set_mode):
            worker=threading.Thread(target=lambda:result.append(self.request('/api/mode',{'mode':'off'})),daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.op.open(self.base+'/api/status',timeout=1) as r:
                    self.assertEqual(r.status,200)
                    self.assertEqual(json.load(r)['version'],'1.0.1')
            finally:release.set();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertEqual(result[0][0],200)


if __name__=='__main__':unittest.main()
