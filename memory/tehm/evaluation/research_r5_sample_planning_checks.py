"""Independent Decimal recomputation and inference-boundary checks."""
from decimal import Decimal, ROUND_CEILING, localcontext
import math
import unittest
from . import research_r5_sample_planning as p


class Checks(unittest.TestCase):
    def test_decimal_recomputation_and_minimal_sufficient_integer(self):
        with localcontext() as ctx:
            ctx.prec=60
            for width in ('0.20','0.15','0.10','0.05'):
                for count in (1,3):
                    h=Decimal(width);alpha=Decimal('0.05')
                    exact=Decimal(4)*(Decimal(2*count)/alpha).ln()/(2*h*h)
                    expected=int(exact.to_integral_value(rounding=ROUND_CEILING))
                    self.assertEqual(p.sufficient_groups(float(width),contrasts=count),expected)
                    # Independently invert the probability bound, not the helper.
                    self.assertLessEqual(2*count*(-Decimal(expected)*h*h/2).exp(),alpha)
                    self.assertGreater(2*count*(-Decimal(expected-1)*h*h/2).exp(),alpha)

    def test_range_and_multiplicity(self):
        self.assertEqual(p.sufficient_groups(.1,contrasts=1),738)
        self.assertEqual(p.sufficient_groups(.1,contrasts=3),958)
        self.assertEqual(p.sufficient_groups(.1,span=1,contrasts=1),185)
        self.assertAlmostEqual(p.radius(32),p.radius(8)/2)

    def test_bad_inputs(self):
        for value in (True,False,0,-1,float('nan'),float('inf'),'0.1',None):
            with self.subTest(value=value),self.assertRaises(ValueError):p.sufficient_groups(value)
        for kwargs in ({'alpha':1},{'alpha':0},{'contrasts':True},{'contrasts':1.5},{'span':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):p.sufficient_groups(.1,**kwargs)
        for groups in (0,-1,True,1.5):
            with self.assertRaises(ValueError):p.radius(groups)

    def test_independence_not_inferred_from_number(self):
        base={'mean':0,'groups':100000,'independent_sampling_verified':True,
            'target_population_sampling_verified':True,'prospectively_fixed':True}
        for field in ('independent_sampling_verified','target_population_sampling_verified','prospectively_fixed'):
            for value in (False,None,1,'true'):
                with self.assertRaises(ValueError):p.empirical_interval(**{**base,field:value})

    def test_interval_range_and_identity(self):
        kwargs={'groups':8,'independent_sampling_verified':True,
            'target_population_sampling_verified':True,'prospectively_fixed':True}
        self.assertEqual(p.empirical_interval(mean=0,**kwargs),[-1,1])
        self.assertEqual(p.empirical_interval(mean=1,**kwargs)[1],1)
        with self.assertRaises(ValueError):p.empirical_interval(mean=2,**kwargs)
        with self.assertRaises(ValueError):p.empirical_interval(mean=float('nan'),**kwargs)

    def test_planning_never_admits_current_data(self):
        result=p.planning_scenarios()
        self.assertFalse(result['applies_to_current_purposive_corpus'])
        self.assertFalse(result['final_test_ready'])
        self.assertIsNone(result['power_claim'])
        self.assertIsNone(result['current_empirical_interval'])
        self.assertEqual(result['new_tasks_authorized'],0)
        self.assertEqual(result['model_calls_authorized'],0)
        self.assertGreater(result['eight_independent_groups_hypothetical_radius'],1)


if __name__=='__main__':unittest.main()
