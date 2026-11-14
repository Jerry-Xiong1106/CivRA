from copy import deepcopy
import unittest
from backend.case.models import SourcePointer
from features10_13_support import FeatureFixture


def source(identity,number,**values):
    fields=dict(case_number=number,decision_date="2025-01-02",court="合成法院",dispute_type="交通事故",**values)
    fields={k:v for k,v in fields.items() if v is not None}
    text="；".join(f"{k}：{v}" for k,v in fields.items())
    return dict(source_id=identity,version="v1",source_type="case",title="合成案例",locator="测试段落",text=text,
        court="合成法院",decision_date="2025-01-02",statistics={k:dict(value=v,quote=f"{k}：{v}") for k,v in fields.items()})


class StatisticsTests(FeatureFixture,unittest.TestCase):
    def run_stats(self,sources,**extra):
        mapping={(s["source_id"],s["version"]):s for s in sources}
        self.service.external_source_resolver=lambda p:deepcopy(mapping[(p.source_id,p.source_version)])
        selections=[dict(source_ref=SourcePointer(source_id=k[0],source_version=k[1]),expected_kind="case") for k in mapping]
        return self.service.build_case_statistics(self.request(selections=selections,**extra))

    def test_groups_counts_denominator_and_unknown_location(self):
        result=self.run_stats([source("a","A",court_region="广东"),source("b","B",court_region="广东"),source("c","C")])
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertEqual((result.data.sample_size,result.data.known_values,result.data.missing_values),(3,2,1))
        self.assertEqual({r["value"]:r["count"] for r in result.data.records},{"广东":2,None:1})
        self.assertTrue(all(r["denominator"]==3 for r in result.data.records))
        self.assertIn("不是全部司法案件或胜诉概率",result.data.scope)

    def test_regions_are_not_inferred_from_court_or_each_other(self):
        result=self.run_stats([source("a","A",court_region="广东",accident_region="广西")],group_by="party_region")
        self.assertEqual(result.data.missing_values,1)
        self.assertIsNone(result.data.records[0]["value"])

    def test_literal_unknown_region_is_counted_as_missing(self):
        result=self.run_stats([source("a","A",court_region="未知")])
        self.assertEqual(result.data.missing_values,1)
        self.assertEqual(result.data.known_values,0)

    def test_case_aliases_are_collapsed_not_counted_twice(self):
        result=self.run_stats([source("a","A",court_region="广东"),source("b","A",court_region="广东")])
        self.assertEqual(result.data.sample_size,1)
        self.assertEqual(len(result.data.duplicate_sources),1)
        self.assertEqual(len(result.data.samples[0]["source_refs"]),2)

    def test_conflicting_related_cases_require_selection(self):
        a=source("a","A",court_region="广东");b=source("b","B",court_region="广西")
        a["case_group_id"]=b["case_group_id"]="group"
        result=self.run_stats([a,b])
        self.assertEqual(result.data.sample_size,0)
        self.assertEqual(result.data.excluded[0]["reason"],"related_case_records_disagree_select_one_record")

    def test_same_case_number_with_conflicting_group_ids_not_double_counted(self):
        a=source("a","A");b=source("b","A")
        a["case_group_id"]="one";b["case_group_id"]="two"
        result=self.run_stats([a,b])
        self.assertEqual(result.data.sample_size,0)

    def test_multiple_versions_are_not_silently_selected(self):
        a=source("a","A");b=deepcopy(a);b["version"]="v2"
        result=self.run_stats([a,b])
        self.assertEqual(result.data.sample_size,0)
        self.assertTrue(all(r["reason"]=="multiple_unresolved_versions" for r in result.data.excluded))

    def test_unlocated_metadata_is_missing_and_case_number_required(self):
        a=source("a","A",court_region="广东");a["statistics"]["court_region"]["value"]="编造地区"
        b=source("b","B");b["statistics"].pop("case_number")
        result=self.run_stats([a,b])
        self.assertEqual((result.data.sample_size,result.data.missing_values),(1,1))
        self.assertTrue(result.data.samples[0]["warnings"])
        self.assertEqual(result.data.excluded[0]["reason"],"missing_located_case_number")

    def test_filters_use_distinct_location_and_preserve_unknown_exclusions(self):
        result=self.run_stats([source("a","A",accident_region="广东"),source("b","B",court_region="广东"),
            source("c","C",accident_region="广西")],filters=dict(accident_region="广东",year_from=2025,year_to=2025),group_by="year")
        self.assertEqual(result.data.sample_size,1)
        self.assertEqual(result.data.eligible_before_filters,3)
        self.assertEqual(result.data.records[0]["value"],"2025")
        self.assertEqual({r["reason"] for r in result.data.excluded},{"filter_missing","filter_mismatch"})

    def test_invalid_filter_range_and_non_case_selection_rejected(self):
        result=self.run_stats([],filters=dict(year_from=2026,year_to=2020))
        self.assertEqual(result.error.code,"invalid_request")
        result=self.service.build_case_statistics(self.request(selections=[dict(source_ref=dict(source_id="law",source_version="1"),expected_kind="statute")]))
        self.assertEqual(result.error.code,"invalid_request")

    def test_awards_preserve_loss_currency_and_quote_without_total(self):
        a=source("a","A")
        a["text"]+="；维修费裁判金额为3000.00元；医疗费裁判金额为200.00美元"
        a["awards"]=[dict(loss_type="维修费",amount="3000.00",currency="CNY",quote="维修费裁判金额为3000.00元"),
            dict(loss_type="医疗费",amount="200.00",currency="USD",quote="医疗费裁判金额为200.00美元")]
        result=self.run_stats([a])
        self.assertEqual(len(result.data.samples[0]["awards"]),2)
        self.assertNotIn("total",result.data.model_dump())
        self.assertFalse(result.data.samples[0]["semantic_metadata_checked"])

    def test_more_than_thirty_sources_are_reviewed_in_batches(self):
        result=self.run_stats([source(f"s{i}",f"case{i}",court_region="广东") for i in range(35)])
        self.assertEqual(result.data.sample_size,35,result.error)
        self.assertEqual(len(result.source_refs),35)

    def test_source_failure_empty_and_replay(self):
        self.service.external_source_resolver=lambda _:(_ for _ in ()).throw(OSError())
        request=self.request(selections=[dict(source_ref=dict(source_id="offline",source_version="1"),expected_kind="case")])
        first=self.service.build_case_statistics(request)
        self.assertEqual(first.status,"empty",first.error)
        self.assertEqual(first.data.excluded[0]["reason"],"source_unavailable")
        self.assertEqual(self.service.build_case_statistics(request),first)

    def test_evidence_graph_direct_material_links_and_conflict_flags(self):
        graph=self.service.build_visualization(self.request(chart_type="evidence_graph"))
        self.assertTrue(any(e["type"]=="claim_material" for e in graph.data.links))
        self.assertTrue(any(n.get("conflict_finding_ids") for n in graph.data.records))
        ids={n["id"] for n in graph.data.records}
        self.assertTrue(all(e["from"] in ids and e["to"] in ids and e["legally_proven"] is False for e in graph.data.links))
