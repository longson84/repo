import json
import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class DashboardUiTests(unittest.TestCase):
    def render(self, expression):
        harness = r"""
const fs = require('fs');
const vm = require('vm');
function element() {
  return new Proxy({
    value: 'current', hidden: false, innerHTML: '', textContent: '', disabled: false,
    classList: {add(){}, remove(){}, toggle(){}, contains(){return false;}},
    focus(){}, setSelectionRange(){}, querySelectorAll(){return [];},
  }, {get(target, key) { return key in target ? target[key] : null; }, set(target, key, value) { target[key] = value; return true; }});
}
const nodes = new Map();
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
  querySelectorAll() { return []; }, addEventListener() {}, activeElement: element(),
};
const context = {
  console, document, location: {hash: '#/', search: '', pathname: '/'},
  history: {replaceState() {}}, localStorage: {getItem(){return '';}, setItem(){}, removeItem(){}},
  fetch: async () => ({ok: false, json: async () => ({error: 'offline'})}),
  AbortController, URLSearchParams, Date, JSON, Math, Set, Map,
  setTimeout(){return 0;}, clearTimeout(){},
};
context.window = context;
context.globalThis = context;
context.addEventListener = () => {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('dist/project_groups.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('dist/app.js', 'utf8'), context);
const output = vm.runInContext(process.argv[1], context);
process.stdout.write(JSON.stringify(output));
"""
        completed = subprocess.run(
            ["node", "-e", harness, expression],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return json.loads(completed.stdout)

    def test_model_rows_render_stable_model_toggle_and_nested_project(self):
        html = self.render(
            "modelUsageRows([{provider:'openai',model:'gpt-test',input_tokens:10,"
            "cached_tokens:0,output_tokens:2,cache_write_tokens:0,events:1,"
            "by_project:[{project_id:'alpha',project_name:'Alpha',project:'Alpha',"
            "input_tokens:10,cached_tokens:0,output_tokens:2,cache_write_tokens:0,events:1}]}],"
            "{input_tokens:10,cached_tokens:0,output_tokens:2,cache_write_tokens:0},true)"
        )
        self.assertIn('data-ai-model-toggle="openai::gpt-test"', html)
        self.assertIn('data-project-id="alpha"', html)
        self.assertIn("Alpha", html)

    def test_repository_hierarchy_renders_stable_ids_and_workspace_status(self):
        html = self.render(
            "statsHierarchyRows([{project_id:'product',project_name:'Product',commitCount:3,"
            "commits7d:1,commits30d:2,lastCommitAt:'2026-10-01',repositories:[{"
            "repository_id:'remote:example/product',repository_name:'product',commitCount:3,"
            "commits7d:1,commits30d:2,lastCommitAt:'2026-10-01',workspaces:[{"
            "workspace_id:'/work/product',workspace_name:'product',path:'/work/product',"
            "branch:'main',modified:1,untracked:0,ahead:0,behind:0,fresh:false,primary:true}]}]}],true)"
        )
        self.assertIn('data-stats-project-toggle="product"', html)
        self.assertIn('data-stats-repository-toggle="remote:example/product"', html)
        self.assertIn('data-workspace-id="/work/product"', html)
        self.assertIn("main", html)
        self.assertIn("1 dirty", html)

    def test_attribution_quality_lists_counts_paths_and_warning_codes(self):
        html = self.render(
            "attributionQualityHtml({attribution:{git:1,configured:2,path:0,unattributed:1},"
            "identity_warnings:[{code:'workspace_missing',workspace_id:'/old/worktree'}]},"
            "[{workspace_id:'/old/worktree',workspace_name:'worktree',identity_confidence:'unattributed'}])"
        )
        self.assertIn("2 configured", html)
        self.assertIn("1 unattributed", html)
        self.assertIn("/old/worktree", html)
        self.assertIn("workspace_missing", html)

    def test_repository_hierarchy_honors_the_active_sort(self):
        html = self.render(
            "(boardSortKey='name',boardSortDir=1,statsHierarchyRows(["
            "{project_id:'z',project_name:'Zulu',commitCount:1,commits7d:0,commits30d:0,repositories:[]},"
            "{project_id:'a',project_name:'Alpha',commitCount:1,commits7d:0,commits30d:0,repositories:[]}]))"
        )
        self.assertLess(html.index("Alpha"), html.index("Zulu"))

    def test_toggle_focus_is_restored_after_rerender(self):
        focused = self.render(
            "(() => { let focused=false; document.querySelectorAll=() => ["
            "{dataset:{aiModelToggle:'provider::model'},focus(){focused=true}}];"
            "restoreToggleFocus('[data-ai-model-toggle]','aiModelToggle','provider::model');"
            "return focused; })()"
        )
        self.assertTrue(focused)

    def test_ai_numeric_sort_defaults_descending_then_reverses(self):
        state = self.render(
            "(() => { aiUsageSortKey=null; aiUsageSortDir=1;"
            "setAiUsageSort('cached_tokens'); const first=[aiUsageSortKey,aiUsageSortDir];"
            "setAiUsageSort('cached_tokens'); return [first,[aiUsageSortKey,aiUsageSortDir]]; })()"
        )
        self.assertEqual(state, [["cached_tokens", -1], ["cached_tokens", 1]])

    def test_ai_model_rows_sort_parents_and_nested_projects_by_cached_tokens(self):
        html = self.render(
            "(aiUsageSortKey='cached_tokens',aiUsageSortDir=-1,modelUsageRows(["
            "{provider:'p',model:'low',input_tokens:0,cached_tokens:10,output_tokens:0,cache_write_tokens:0,events:1,by_project:[]},"
            "{provider:'p',model:'high',input_tokens:0,cached_tokens:20,output_tokens:0,cache_write_tokens:0,events:1,by_project:["
            "{project_id:'small',project_name:'Small',input_tokens:0,cached_tokens:2,output_tokens:0,cache_write_tokens:0,events:1},"
            "{project_id:'large',project_name:'Large',input_tokens:0,cached_tokens:8,output_tokens:0,cache_write_tokens:0,events:1}]}],"
            "{input_tokens:0,cached_tokens:30,output_tokens:0,cache_write_tokens:0},true))"
        )
        self.assertLess(html.index("p / high"), html.index("p / low"))
        self.assertLess(html.index("Large"), html.index("Small"))

    def test_ai_label_sort_and_sortable_header_are_accessible(self):
        result = self.render(
            "(() => { aiUsageSortKey='label'; aiUsageSortDir=1; return {"
            "rows:usageDataRows([{tool:'Zulu'},{tool:'Alpha'}],x=>x.tool,0),"
            "header:aiUsageHeaderCell('Name','label')}; })()"
        )
        self.assertLess(result["rows"].index("Alpha"), result["rows"].index("Zulu"))
        self.assertIn('data-ai-usage-sort="label"', result["header"])
        self.assertIn('aria-sort="ascending"', result["header"])

    def test_project_manager_model_keeps_tracking_separate(self):
        rows = self.render(
            "[...projectManagerModel(['/work/a','/legacy/path'],["
            "{workspace_id:'/work/a',path:'/work/a',workspace_name:'Alpha',"
            "repository_id:'remote:example/alpha',repository_name:'alpha',"
            "project_id:'product',project_name:'Product'}],["
            "{workspace_id:'/work/b',path:'/work/b',workspace_name:'Beta',"
            "repository_id:'remote:example/beta',repository_name:'beta',"
            "project_id:'remote:example/beta',project_name:'beta'}]).values()]"
        )

        by_workspace = {row["workspace_id"]: row for row in rows}
        self.assertTrue(by_workspace["/work/a"]["tracked"])
        self.assertEqual(by_workspace["/work/a"]["repository_id"], "remote:example/alpha")
        self.assertFalse(by_workspace["/work/b"]["tracked"])
        self.assertEqual(by_workspace["/work/b"]["repository_id"], "remote:example/beta")
        self.assertTrue(by_workspace["/legacy/path"]["tracked"])
        self.assertIsNone(by_workspace["/legacy/path"]["repository_id"])

    def test_project_manager_rows_select_repository_identity_once(self):
        html = self.render(
            "projectManagerRows(new Map(["
            "['/work/a',{workspace_id:'/work/a',workspace_name:'Alpha',path:'/work/a',"
            "repository_id:'remote:example/shared',repository_name:'shared',project_id:'group',"
            "project_name:'Group',tracked:true}],"
            "['/work/a-copy',{workspace_id:'/work/a-copy',workspace_name:'Alpha copy',path:'/work/a-copy',"
            "repository_id:'remote:example/shared',repository_name:'shared',project_id:'group',"
            "project_name:'Group',tracked:false}],"
            "['/work/unknown',{workspace_id:'/work/unknown',workspace_name:'Unknown',path:'/work/unknown',"
            "repository_id:null,repository_name:null,project_id:null,project_name:null,tracked:true}]"
            "]),new Set(['remote:example/shared']),{version:1,projects:[]})"
        )

        self.assertEqual(html.count('data-group-repository="remote:example/shared"'), 2)
        self.assertEqual(
            len(
                re.findall(
                    r'<input[^>]*data-group-repository="remote:example/shared"[^>]*checked',
                    html,
                )
            ),
            2,
        )
        self.assertIn('aria-label="Select shared for grouping"', html)
        self.assertIn('aria-label="Track Alpha"', html)
        self.assertIn('data-track-workspace="/work/a-copy"', html)
        self.assertIn('data-current-project="group"', html)
        self.assertRegex(
            html,
            r'<input[^>]*data-group-repository=""[^>]*aria-label="Select Unknown for grouping"[^>]*disabled',
        )
        self.assertIn('data-track-workspace="/work/unknown"', html)


if __name__ == "__main__":
    unittest.main()
