import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"host"))
from geometry import Calibration,Lens,project


def fixture(**changes):
    return {"width_m":5,"height_m":5,"image_size":[640,480],"points":[[60,420],[580,420],[400,90],[240,90]],"device_id":"test-camera","geometry_id":"640x480-rgb-v1",**changes}


class GeometryTests(unittest.TestCase):
    def test_known_perspective_center_is_not_pixel_quad_center(self):
        c=Calibration(fixture())
        p=c.to_world([320,167.64705882352942])
        self.assertAlmostEqual(p["x"],2.5,places=8)
        self.assertAlmostEqual(p["y"],2.5,places=8)
        self.assertTrue(p["inside"])
        self.assertGreater(abs(c.to_world([320,255])["y"]-2.5),1)

    def test_rectangle_size_changes_metric_scale(self):
        c=Calibration(fixture(width_m=10,height_m=3))
        p=c.to_world([320,167.64705882352942])
        self.assertAlmostEqual(p["x"],5,places=8)
        self.assertAlmostEqual(p["y"],1.5,places=8)

    def test_dense_interior_roundtrip(self):
        c=Calibration(fixture())
        for x in range(11):
            for y in range(11):
                p=c.to_world(c.to_pixel([x/2,y/2]))
                self.assertAlmostEqual(p["x"],x/2,places=8)
                self.assertAlmostEqual(p["y"],y/2,places=8)
                self.assertTrue(p["inside"])

    def test_outside_region_not_clamped_into_it(self):
        self.assertFalse(Calibration(fixture()).to_world([10,450])["inside"])

    def test_point_order_and_degeneracy_rejected(self):
        for points in [ [[60,420],[400,90],[580,420],[240,90]], [[10,10],[100,10],[200,10],[300,10]], [[60,420]]*4 ]:
            with self.assertRaises(ValueError):Calibration(fixture(points=points))

    def test_nonfinite_and_invalid_sizes(self):
        for v in [0,-1,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError):Calibration(fixture(width_m=v))
        with self.assertRaises(ValueError):Calibration(fixture(created_at=float('inf')))

    def test_independent_measurement_error_and_reload(self):
        c=Calibration(fixture())
        p=c.to_pixel([2,3]);r=c.check(p,[2.03,3.04])
        self.assertAlmostEqual(r['error_m'],.05,places=8)
        data=c.export()
        self.assertEqual(data['validation']['count'],1)
        self.assertAlmostEqual(data['validation']['rmse_m'],.05,places=8)
        self.assertAlmostEqual(Calibration(data).checks[0]['error_m'],.05,places=8)

    def test_calibration_corners_and_duplicate_checks_are_not_validation(self):
        c=Calibration(fixture())
        with self.assertRaises(ValueError):c.check(c.points[0],[0,0])
        p=c.to_pixel([2,3]);c.check(p,[2,3])
        with self.assertRaises(ValueError):c.check(p,[2,3])

    def test_lens_correction_matches_analytic_distortion(self):
        cfg={"model":"brown","image_size":[640,480],"fx":480,"fy":475,"cx":320,"cy":240,"dist":[-.2,.03,.001,-.002,.005]}
        lens=Lens(cfg,[640,480])
        # Generate a ground/pinhole mapping independently from the fitted matrix.
        world_to_ideal=[[90,15,70],[0,-50,410],[0,.1,1]]
        world=[[0,0],[5,0],[5,5],[0,5]]
        pixels=[lens.distort(project(world_to_ideal,p)) for p in world]
        c=Calibration(fixture(points=pixels,lens=cfg))
        for w in [[1,1],[2.5,2.5],[4,4],[.2,4.8]]:
            pixel=lens.distort(project(world_to_ideal,w));got=c.to_world(pixel)
            self.assertAlmostEqual(got['x'],w[0],places=6)
            self.assertAlmostEqual(got['y'],w[1],places=6)

    def test_lens_resolution_and_model_mismatch(self):
        with self.assertRaises(ValueError):Lens({'model':'fisheye'},[640,480])
        cfg={"fx":480,"fy":480,"cx":320,"cy":240,"dist":[0,0,0,0],"image_size":[320,240]}
        with self.assertRaises(ValueError):Lens(cfg,[640,480])

    def test_grid_bounded_and_matches_metric_positions(self):
        c=Calibration(fixture());lines=c.grid()
        self.assertEqual(len(lines),22)
        for line in lines:
            for p in line['points']:
                got=c.to_world(p)
                self.assertAlmostEqual(got['x' if line['axis']==0 else 'y'],line['value'],places=7)
        self.assertLessEqual(len(Calibration(fixture(width_m=1000,height_m=1000)).grid()),162)


if __name__=='__main__':unittest.main()
