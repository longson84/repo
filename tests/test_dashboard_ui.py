import json
import pathlib
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


if __name__ == "__main__":
    unittest.main()
