import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from backend.knowledge.corpus import load_provisions
from evaluation.runners.pilot import assert_current_index


class IndexGuardTests(unittest.TestCase):
    def test_corpus_change_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'data/processed').mkdir(parents=True)
            (root/'data/index').mkdir(parents=True)
            corpus=root/'data/processed/law.jsonl'
            corpus.write_text(json.dumps({'provision_id':'one','text':'original'})+'\n')
            provisions=load_provisions([corpus])
            digest=hashlib.sha256(json.dumps(provisions,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            config={'index_version':'1.0','dimensions':2,'corpus_sha256':digest}
            np.savez_compressed(root/'data/index/vector_metadata.npz',vectors=np.array([[1.,0.]]),
                                metadata=json.dumps({'config':config,'provisions':provisions}))
            assert_current_index(root)
            corpus.write_text(json.dumps({'provision_id':'one','text':'changed'})+'\n')
            with self.assertRaisesRegex(ValueError,'Corpus changed'):
                assert_current_index(root)


if __name__=='__main__': unittest.main()
