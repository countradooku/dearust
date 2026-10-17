import json
from pathlib import Path
import tempfile
import unittest
from cmake_graph import capture


class CMakeGraphTests(unittest.TestCase):
    def test_preserves_target_passes_flags_install_and_generated_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            build=Path(temporary)/'source-build'
            reply=build/'.cmake/api/v1/reply'
            reply.mkdir(parents=True)
            source=str(Path(temporary)/'source')
            model={'paths':{'source':source,'build':str(build)},'configurations':[{
                'name':'','targets':[{'id':'pass1-id','jsonFile':'target-pass1.json'},{'id':'pass2-id','jsonFile':'target-pass2.json'}],
                'directories':[{'jsonFile':'directory.json'}]}]}
            target={'name':'library_x86_64_firstpass','type':'SHARED_LIBRARY','id':'pass1-id',
                'sources':[{'path':str(build)+'/generated.S','isGenerated':True}],
                'dependencies':[{'id':'pass2-id'}],
                'compileGroups':[{'compileCommandFragments':[{'fragment':'-O2 -I'+source+'/include -target x86_64-apple-darwin'}]}],
                'link':{'commandFragments':[{'fragment':'-install_name /System/Library/lib.dylib'}]},
                'install':{'prefix':{'path':'/usr/local'},'destinations':[{'path':'libexec/darling/usr/lib'}]}}
            objects={'codemodel.json':model,'target-pass1.json':target,
                'target-pass2.json':{'name':'library_x86_64_secondpass','type':'UTILITY','id':'pass2-id'},
                'directory.json':{'installers':[{'type':'script','scriptFile':source+'/install.cmake'}]}}
            index={'cmake':{'version':{'major':4}},'objects':[{'kind':'codemodel','version':{'major':2},'jsonFile':'codemodel.json'}]}
            for filename,data in objects.items():(reply/filename).write_text(json.dumps(data))
            (reply/'index-2026-01.json').write_text(json.dumps(index))
            graph=capture(build)
            self.assertEqual(graph['counts']['targets'],2)
            self.assertEqual(graph['counts']['generated_source_records'],1)
            obj=graph['objects']['target-pass1.json']
            self.assertEqual(obj['name'],target['name'])
            self.assertEqual(obj['sources'][0]['path'],'$BUILD/generated.S')
            self.assertIn('-I$SOURCE/include',obj['compileGroups'][0]['compileCommandFragments'][0]['fragment'])
            self.assertEqual(obj['link'],target['link'])
            self.assertEqual(obj['install'],target['install'])
            self.assertEqual(obj['dependencies'],target['dependencies'])
            (reply/'index-2026-02.json').write_text(json.dumps(index))
            self.assertEqual(graph,capture(build))

    def test_missing_reply_is_explicit_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError,'no File API reply'):
                capture(Path(temporary))


if __name__=='__main__':unittest.main()
