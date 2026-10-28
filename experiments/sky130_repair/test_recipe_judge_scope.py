import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from judge_recipe_ab import validate_arm_scope


class RecipeJudgeScopeTests(unittest.TestCase):
    def fixture(self):
        key=dict(symptom_id='symptom',design_class='logic/small',platform='sky130hd',strategy='pin_pdn_clearance')
        h6=hashlib.sha1('|'.join(key[k] for k in ('symptom_id','design_class','platform','strategy')).encode()).hexdigest()[:6].upper()
        arm=dict(subject='/subjects/filter',project='/trials/filter_abB_pin_pdn_'+h6+'_0',role='B',repeat=0,source_repository='original-family')
        plan=dict(key=key,planner=dict(key=key.copy(),match_level='current_exhibitor_pooled_class',designs=[dict(project_path=arm['subject'])]))
        row=dict(platform='sky130hd',design_class='logic/medium',design_family='original-family')
        return plan,arm,row

    def test_planned_pooling_preserves_actual_class(self):
        plan,arm,row=self.fixture();before=copy.deepcopy(row)
        validate_arm_scope(plan,arm,row)
        self.assertEqual(row,before)
        plan['planner']['match_level']='current_exhibitor'
        with self.assertRaisesRegex(ValueError,'without planner pooling'):validate_arm_scope(plan,arm,row)

    def test_pooling_never_allows_other_platform_family_or_subject(self):
        for field,value in [('platform','nangate45'),('design_family','foreign')]:
            plan,arm,row=self.fixture();row[field]=value
            with self.assertRaises(ValueError):validate_arm_scope(plan,arm,row)
        plan,arm,row=self.fixture();arm['subject']='/subjects/foreign'
        with self.assertRaisesRegex(ValueError,'not selected'):validate_arm_scope(plan,arm,row)

    def test_full_key_binding_survives_same_strategy_and_platform(self):
        for field in ('symptom_id','design_class'):
            plan,arm,row=self.fixture();plan['key'][field]='other';plan['planner']['key']=plan['key'].copy()
            with self.assertRaisesRegex(ValueError,'full recipe key'):validate_arm_scope(plan,arm,row)
        plan,arm,row=self.fixture();arm['role']='A'
        with self.assertRaisesRegex(ValueError,'full recipe key'):validate_arm_scope(plan,arm,row)

    def test_publication_requires_completed_private_promotion(self):
        from publish_verified_recipe import prepare
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source';source.mkdir();dest=Path(d)/'durable'
            (source/'plan.json').write_text(json.dumps(dict(arms=[{}]*8)))
            (source/'judgement.json').write_text(json.dumps(dict(lifecycle_status='candidate')))
            with self.assertRaisesRegex(ValueError,'not promoted'):prepare(source,dest)
            self.assertFalse(dest.exists())

    def test_existing_durable_evidence_cannot_be_reused_for_another_plan(self):
        from publish_verified_recipe import prepare,sha
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source';source.mkdir();dest=Path(d)/'durable';dest.mkdir()
            plan=dict(key={'strategy':'pin_pdn_clearance'},arms=[{}]*8)
            (source/'plan.json').write_text(json.dumps(plan))
            (source/'judgement.json').write_text(json.dumps(dict(lifecycle_status='promoted')))
            old=dict(plan,arms=[{'unrelated':True}]*8);(dest/'original_plan.json').write_text(json.dumps(old))
            (dest/'durable_evidence.json').write_text(json.dumps(dict(key=plan['key'],files={'original_plan.json':sha(dest/'original_plan.json')})))
            with self.assertRaisesRegex(ValueError,'different plan'):prepare(source,dest)


if __name__=='__main__':unittest.main()
