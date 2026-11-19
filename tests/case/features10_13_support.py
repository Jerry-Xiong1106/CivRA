from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.models import BusinessOutput, CaseSnapshot


class FeatureFixture:
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.case=self.repo.create_case(load_development_case())
        self.service=CaseService(self.repo)

    def context(self,name="test"):
        return dict(request_id=name,case_id=self.case.case_id,experiment_run_id=None,deadline_at=None)

    def request(self,name="test",*,snapshot=True,**extra):
        case=self.repo.get_case(self.case.case_id)
        return dict(context=self.context(name),case_version=case.case_version,
            **({"case_snapshot":case} if snapshot else {}),**extra)

    def output(self,name="candidate",**extra):
        case=self.repo.get_case(self.case.case_id)
        output=BusinessOutput(output_id=name,output_version=1,case_id=case.case_id,case_version=case.case_version,
            output_type="analysis",text="合成输出",**extra)
        self.service.assets.save_output(output)
        return output

    def advance(self,mutate=None):
        case=self.repo.get_case(self.case.case_id)
        raw=case.model_dump(mode="json")
        raw.update(case_version=case.case_version+1,updated_at=datetime.now(timezone.utc).isoformat())
        if mutate:mutate(raw)
        return self.repo.replace_case(CaseSnapshot.model_validate(raw),expected_case_version=case.case_version)
