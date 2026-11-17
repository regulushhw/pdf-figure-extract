"""Real scale invariants, independent ticks, and nonlinear round trips."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from review_points import axis_fit, value_at, data_to_pixel, value_at_position

class LogCalibrationTests(unittest.TestCase):
    def calibration(self, **kwargs):
        return axis_fit({'coordinate':'y','kind':'log10','unit':'m3/s',
                         'ticks':[[0,100],[200,1]], **kwargs})

    def test_log_midpoint_not_linear_midpoint(self):
        c=self.calibration(check_ticks=[[100,10]])
        self.assertAlmostEqual(value_at([17,100],c),10)
        self.assertAlmostEqual(value_at_position(100,c),10)
        self.assertEqual(data_to_pixel([17,33],c,10)[0],17)
        self.assertAlmostEqual(data_to_pixel([17,33],c,10)[1],100)

    def test_independent_wrong_decade_rejected(self):
        with self.assertRaises(ValueError):
            self.calibration(check_ticks=[[100,1]])

    def test_invalid_domains_and_unknown_kind_rejected(self):
        for v in [0,-1,float('inf')]:
            with self.assertRaises(ValueError):
                data_to_pixel([17,100],self.calibration(),v)
        with self.assertRaises(ValueError):
            axis_fit({'coordinate':'x','kind':'probability','unit':'%', 'ticks':[[0,1],[100,99]]})

    def test_many_values_roundtrip_on_both_axis_directions(self):
        for ticks in [[[0,100],[200,1]],[[0,1],[200,100]]]:
            for coordinate in ['x','y']:
                c=axis_fit({'coordinate':coordinate,'kind':'log10','unit':'u','ticks':ticks})
                for v in [1,2,5,10,50,100]:
                    p=data_to_pixel([71,83],c,v)
                    self.assertAlmostEqual(value_at(p,c),v)

if __name__=='__main__':unittest.main()
