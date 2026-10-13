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
    value: 'current', hidden: false, innerHTML: '', textContent: '', disabled: false, style: {},
    classList: {add(){}, remove(){}, toggle(){}, contains(){return false;}},
    focus(){}, setSelectionRange(){}, querySelectorAll(){return [];},
  }, {get(target, key) { return key in target ? target[key] : null; }, set(target, key, value) { target[key] = value; return true; }});
}
const nodes = new Map();
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
  querySelectorAll() { return []; }, querySelector() { return null; },
  addEventListener() {}, activeElement: element(),
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
Promise.resolve(output).then((value) => process.stdout.write(JSON.stringify(value)));
"""
        try:
            completed = subprocess.run(
                ["node", "-e", harness, expression],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as error:
            self.fail(error.stderr)
        return json.loads(completed.stdout)

    def test_pull_all_button_posts_pull_for_ready_repos(self):
        markup = (ROOT / "dist" / "index.html").read_text()
        self.assertIn('id="pullAll"', markup)
        result = self.render(
            "(() => { paired=true; paths=['/work/a']; busy=false; statsBusy=false;"
            "repos=[{path:'/work/a',name:'a',fresh:true,error:null,upstream:'origin/main',"
            "ahead:0,behind:1,modified:0,untracked:0}]; let url=null;"
            "fetch=async(href)=>{ if(String(href).includes('/api/pull')) url=href;"
            "return {ok:true,status:200,json:async()=>String(href).includes('/api/pull')?"
            "{machine:'test',scannedAt:'2026-10-11T00:00:00Z',pulled:1,"
            "repositories:[{path:'/work/a',name:'a',fresh:true,ahead:0,behind:0,"
            "modified:0,untracked:0}]}:{repositories:[],projects:[]}};};"
            "return runPull().then(()=>({url,behind:repos[0].behind})); })()"
        )
        self.assertTrue(result["url"].endswith("/api/pull"))
        self.assertEqual(result["behind"], 0)

    def test_ready_row_shows_pull_button_and_posts_that_path(self):
        html = self.render(
            "(() => { $('search').value=''; paired=true; scannedAt='2026-10-11T00:00:00Z';"
            "paths=['/work/ready','/work/dirty']; busy=false; statsBusy=false;"
            "repos=["
            "{path:'/work/ready',name:'ready',branch:'main',fresh:true,error:null,"
            "upstream:'origin/main',ahead:0,behind:2,modified:0,untracked:0},"
            "{path:'/work/dirty',name:'dirty',branch:'main',fresh:true,error:null,"
            "upstream:'origin/main',ahead:0,behind:2,modified:1,untracked:0}"
            "]; render(); return $('rows').innerHTML; })()"
        )
        self.assertIn('data-path="/work/ready"', html)
        self.assertIn("row-pull", html)
        self.assertRegex(html, r'data-path="/work/ready"[^>]*>Pull')
        self.assertNotRegex(html, r'data-path="/work/dirty"[^>]*>Pull')

        result = self.render(
            "(() => { paired=true; paths=['/work/ready']; busy=false; statsBusy=false;"
            "repos=[{path:'/work/ready',name:'ready',fresh:true,error:null,"
            "upstream:'origin/main',ahead:0,behind:2,modified:0,untracked:0}];"
            "let body=null; fetch=async(href,options)=>{"
            "if(String(href).includes('/api/pull')) body=JSON.parse(options.body);"
            "return {ok:true,status:200,json:async()=>String(href).includes('/api/pull')?"
            "{machine:'test',scannedAt:'2026-10-11T00:00:00Z',pulled:1,"
            "repositories:[{path:'/work/ready',name:'ready',fresh:true,ahead:0,behind:0,"
            "modified:0,untracked:0}]}:{repositories:[],projects:[]}};};"
            "return runPull({path:'/work/ready'}).then(()=>body); })()"
        )
        self.assertEqual(result, {"path": "/work/ready"})

    def test_dashboard_has_no_cross_machine_placeholder_ui(self):
        markup = (ROOT / "dist" / "index.html").read_text()
        script = (ROOT / "dist" / "app.js").read_text()

        for removed in (
            'id="machinesNav"',
            'id="machine"',
            "Second machine",
            "across machines",
            "shared between machines",
        ):
            self.assertNotIn(removed, markup)
        self.assertNotIn("$('machinesNav')", script)
        self.assertNotIn("$('machine')", script)
        self.assertNotIn("const other =", script)

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

    def test_tracking_reconciliation_message_reports_paths_and_project_cleanup(self):
        message = self.render(
            "trackingReconciliationMessage({removed_paths:2,"
            "removed_repository_memberships:3,removed_primary_workspaces:1,"
            "removed_workspace_aliases:2,removed_projects:1})"
        )

        self.assertIn("2 saved paths", message)
        self.assertIn("3 repository memberships", message)
        self.assertIn("1 primary workspace", message)
        self.assertIn("2 workspace aliases", message)
        self.assertIn("1 empty project", message)

    def test_detect_panel_renders_availability_columns_only(self):
        html = self.render(
            "(paired=true,aiDetect={version:3,detectedAt:'2026-10-11T00:00:00Z',"
            "summary:{total:1,stores_found:1,found:1},sources:[{id:'claude.code',"
            "tool:'claude-code',surface:'cli',path:'/store',exists:true,size_bytes:10,"
            "notes:'Available',supports:{sessions:true}}]},paintAiDetectPanel())"
        )

        for heading in ("Surface", "Status", "Path", "Size", "Notes"):
            self.assertIn(f"<th>{heading}</th>", html)
        self.assertNotIn("Conversations≈", html)
        self.assertNotIn("Executions≈", html)
        self.assertNotIn("Representations", html)

    def test_ai_panels_explain_selected_github_scope_and_exclusions(self):
        result = self.render(
            "(() => { paired=true; paths=[]; aiSessions={sessions:[],conversation_count:0,"
            "execution_count:0,representation_count:0}; aiSearch={q:'x',hits:[],count:0,"
            "engine:'python'}; aiUsage={aggregatedAt:'2026-10-11T00:00:00Z',"
            "totals:{input_tokens:0,cached_tokens:0,output_tokens:0,cache_write_tokens:0,events:0},"
            "scope:{kind:'selected_github_repositories',repository_count:0,"
            "excluded_event_count:7,excluded_tokens:{input_tokens:70,cached_tokens:0,"
            "output_tokens:7,cache_write_tokens:0,events:7}},by_day:[],by_week:[],"
            "by_month:[],by_year:[],by_model:[],by_project:[],by_surface:[],by_tool:[],"
            "by_workspace:[],quality:{attribution:{git:0,configured:0,path:0,unattributed:0},"
            "identity_warnings:[],reconciliation:{}},caveats:[]};"
            "return {sessions:paintAiSessionsPanel(),search:paintAiSearchPanel(),"
            "tokens:paintAiTokensPanel()};})()"
        )

        for panel in result.values():
            self.assertIn("Selected GitHub repositories", panel)
        self.assertIn(
            "Select a repository with a github.com remote", result["sessions"]
        )
        self.assertIn("7 events excluded", result["tokens"])
        self.assertNotIn(" path ·", result["tokens"])

    def test_empty_scoped_session_response_replaces_loaded_rows(self):
        html = self.render(
            "(() => { paired=true; aiSessions={sessions:[{id:'old-row',tool:'codex'}]};"
            "fetch=async()=>({ok:true,status:200,json:async()=>({sessions:[],count:0,"
            "conversation_count:0,execution_count:0,representation_count:0})});"
            "return runAiSessions().then(()=>paintAiSessionsPanel());})()"
        )

        self.assertNotIn("old-row", html)
        self.assertIn("Select a repository with a github.com remote", html)

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

    def test_language_matrix_aggregates_repositories_into_logical_projects(self):
        matrix = self.render(
            "(() => { statsProjects=[{project_id:'shared',project_name:'Shared',repositories:["
            "{repository_id:'repo:a',repository_name:'Alpha',path:'/work/a',"
            "locByLanguage:[{name:'TypeScript',code:100,complexity:10}]},"
            "{repository_id:'repo:b',repository_name:'Beta',path:'/work/b',"
            "locByLanguage:[{name:'TypeScript',code:40,complexity:4},"
            "{name:'Python',code:60,complexity:6}]}]}]; locMetric='code';"
            "return locLanguageMatrix(); })()"
        )

        self.assertEqual(matrix["languages"], ["TypeScript", "Python"])
        self.assertEqual(len(matrix["rows"]), 1)
        self.assertEqual(matrix["rows"][0]["name"], "Shared")
        self.assertEqual(matrix["rows"][0]["byLang"]["TypeScript"], 140)
        self.assertEqual(matrix["rows"][0]["byLang"]["Python"], 60)
        self.assertEqual(matrix["rows"][0]["total"], 200)
        self.assertEqual(len(matrix["rows"][0]["repositories"]), 2)

    def test_language_matrix_expands_project_into_repository_rows(self):
        html = self.render(
            "(() => { statsProjects=[{project_id:'shared',project_name:'Shared',repositories:["
            "{repository_id:'repo:a',repository_name:'Alpha',path:'/work/a',"
            "locByLanguage:[{name:'TypeScript',code:100,complexity:10}]},"
            "{repository_id:'repo:b',repository_name:'Beta',path:'/work/b',"
            "locByLanguage:[{name:'Python',code:60,complexity:6}]}]}]; locMetric='code';"
            "locExpandedProjects.add('shared'); return locMatrixHtml(); })()"
        )

        self.assertIn('data-loc-project-toggle="shared"', html)
        self.assertIn('class="loc-project-row"', html)
        self.assertIn('class="loc-repository-row board-row"', html)
        self.assertIn('data-path="/work/a"', html)
        self.assertIn("Alpha", html)
        self.assertIn("Beta", html)

    def test_language_project_toggle_expands_and_restores_focus(self):
        result = self.render(
            "(() => { const button={dataset:{locProjectToggle:'shared'},focused:false,"
            "focus(){this.focused=true}}; let redraws=0; document.querySelectorAll=selector=>"
            "selector==='[data-loc-project-toggle]'?[button]:[];"
            "bindLocProjectToggles(()=>{redraws+=1}); button.onclick();"
            "return {expanded:locExpandedProjects.has('shared'),redraws,focused:button.focused}; })()"
        )

        self.assertEqual(result, {"expanded": True, "redraws": 1, "focused": True})

    def test_language_project_rows_follow_the_active_language_sort(self):
        html = self.render(
            "(() => { statsProjects=["
            "{project_id:'small',project_name:'Small',repositories:[{repository_id:'repo:s',"
            "repository_name:'Small repo',path:'/small',locByLanguage:[{name:'Python',code:10}]}]},"
            "{project_id:'large',project_name:'Large',repositories:[{repository_id:'repo:l',"
            "repository_name:'Large repo',path:'/large',locByLanguage:[{name:'Python',code:100}]}]}"
            "]; locMetric='code'; locSortKey='Python'; locSortDir=-1; return locMatrixHtml(); })()"
        )

        self.assertLess(html.index("Large"), html.index("Small"))

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
        self.assertEqual(
            by_workspace["/work/a"]["repository_id"], "remote:example/alpha"
        )
        self.assertFalse(by_workspace["/work/b"]["tracked"])
        self.assertEqual(
            by_workspace["/work/b"]["repository_id"], "remote:example/beta"
        )
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
            "]),new Set(['remote:example/shared']),{version:1,projects:[{id:'group',name:'Group',"
            "repositories:['remote:example/shared'],primary_workspaces:{},workspace_aliases:[]}]})"
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

    def test_project_manager_disables_grouping_for_untracked_repository(self):
        html = self.render(
            "projectManagerRows(new Map(["
            "['/work/tracked',{workspace_id:'/work/tracked',workspace_name:'Tracked',"
            "path:'/work/tracked',repository_id:'repo:tracked',repository_name:'tracked',tracked:true}],"
            "['/work/untracked',{workspace_id:'/work/untracked',workspace_name:'Untracked',"
            "path:'/work/untracked',repository_id:'repo:untracked',repository_name:'untracked',"
            "tracked:false}]]),new Set(),{version:1,projects:[]})"
        )

        untracked = re.search(
            r'<input[^>]*data-group-repository="repo:untracked"[^>]*>', html
        )
        tracked = re.search(
            r'<input[^>]*data-group-repository="repo:tracked"[^>]*>', html
        )
        self.assertIsNotNone(untracked)
        self.assertIsNotNone(tracked)
        self.assertIn("disabled", untracked.group(0))
        self.assertNotIn("disabled", tracked.group(0))

    def test_project_manager_disables_controls_while_selection_saves(self):
        html = self.render(
            "projectManagerRows(new Map([['/work/a',{workspace_id:'/work/a',"
            "workspace_name:'A',path:'/work/a',repository_id:'repo:a',"
            "repository_name:'a',tracked:true}]]),new Set(),{version:1,projects:[]},true)"
        )

        grouping = re.search(r'<input[^>]*data-group-repository="repo:a"[^>]*>', html)
        tracking = re.search(r'<input[^>]*data-track-workspace="/work/a"[^>]*>', html)
        self.assertIn("disabled", grouping.group(0))
        self.assertIn("disabled", tracking.group(0))

    def test_project_bulk_editor_options(self):
        html = self.render(
            "projectBulkEditor({version:1,projects:["
            "{id:'general',name:'General'},{id:'product',name:'Product'}]},"
            "new Set(['repo:a','repo:b']),'existing')"
        )

        self.assertIn("2 repositories selected", html)
        self.assertIn('class="project-row-actions"', html)
        self.assertIn('value="existing" selected', html)
        self.assertIn('value="new"', html)
        self.assertIn("New project", html)
        self.assertIn('value="standalone"', html)
        self.assertIn("Standalone", html)
        self.assertIn('value="general"', html)
        self.assertIn("General", html)
        self.assertIn('id="projectApply"', html)
        self.assertIn('id="projectCancel"', html)
        self.assertNotRegex(html, r'id="projectTarget"[^>]*hidden')
        self.assertRegex(html, r'<option value="general"[^>]*>General</option>')
        self.assertRegex(html, r'<option value="product"[^>]*>Product</option>')

    def test_existing_assignment_mode_stays_selected_even_without_projects(self):
        result = self.render(
            "({empty:resolveBulkAssignmentMode('existing', []),"
            "withProjects:resolveBulkAssignmentMode('existing', [{id:'general'}]),"
            "keepNew:resolveBulkAssignmentMode('new', [{id:'general'}]),"
            "keepStandalone:resolveBulkAssignmentMode('standalone', [])})"
        )

        self.assertEqual(
            result,
            {
                "empty": "existing",
                "withProjects": "existing",
                "keepNew": "new",
                "keepStandalone": "standalone",
            },
        )

    def test_project_manager_styles_do_not_clip_project_selects(self):
        css = (ROOT / "dist" / "index.html").read_text()
        pane = re.search(
            r"\.project-manager-pane \{(?P<body>.*?)\n      \}",
            css,
            re.S,
        )
        self.assertIsNotNone(pane)
        self.assertNotIn("overflow: auto", pane.group("body"))
        self.assertRegex(
            css,
            r"\.configured-project-list \{[^}]*overflow:\s*auto",
        )

    def test_project_cards_protect_advanced_mappings(self):
        html = self.render(
            "configuredProjectCards({version:1,projects:["
            "{id:'history',name:'History',repositories:['repo:a','repo:hidden'],"
            "workspace_aliases:['/old/a'],primary_workspaces:{'repo:a':'/work/a'}},"
            "{id:'plain',name:'Plain',repositories:['repo:p'],workspace_aliases:[],"
            "primary_workspaces:{}}]},['repo:a','repo:a','repo:p'])"
        )

        self.assertIn('class="configured-project-card"', html)
        self.assertIn("2 repositories", html)
        self.assertRegex(html, r"2\s+visible\s+workspaces")
        self.assertIn("1 not currently visible", html)
        self.assertRegex(
            html,
            r'data-delete-project="history"[^>]*disabled',
        )
        self.assertRegex(
            html,
            r'data-ungroup-project="history"[^>]*disabled',
        )
        self.assertIn("historical aliases or primary workspaces", html)
        self.assertRegex(html, r'data-delete-project="plain"(?![^>]*disabled)')
        self.assertIn('class="project-row-actions"', html)

    def test_project_manager_bindings_keep_grouping_and_tracking_independent(self):
        result = self.render(
            "(() => { const group={dataset:{groupRepository:'repo:shared'},checked:true};"
            "const track={dataset:{trackWorkspace:'/work/a'},checked:false};"
            "document.querySelectorAll=(selector)=>selector==='[data-group-repository]'?[group]:"
            "selector==='[data-track-workspace]'?[track]:[];"
            "const choices=new Map([['/work/a',{workspace_id:'/work/a',"
            "repository_id:'repo:shared',tracked:true}]]);"
            "const selected=new Set(); let trackingWrites=0; let redraws=0; let paneOpens=0;"
            "bindProjectManagerRows(choices,selected,()=>{trackingWrites+=1},()=>{redraws+=1},"
            "()=>{paneOpens+=1});"
            "group.onchange(); const afterGroup={trackingWrites,selected:[...selected],redraws,paneOpens};"
            "track.onchange(); return {afterGroup,afterTrack:{trackingWrites,"
            "tracked:choices.get('/work/a').tracked,selected:[...selected]}}; })()"
        )

        self.assertEqual(
            result["afterGroup"],
            {
                "trackingWrites": 0,
                "selected": ["repo:shared"],
                "redraws": 1,
                "paneOpens": 1,
            },
        )
        self.assertEqual(
            result["afterTrack"],
            {"trackingWrites": 1, "tracked": False, "selected": []},
        )

    def test_project_action_builds_complete_deduplicated_draft(self):
        result = self.render(
            "(() => { const config={version:1,projects:[{id:'old',name:'Old',"
            "repositories:['repo:a'],primary_workspaces:{},workspace_aliases:[]}]};"
            "const draft=stageProjectManagerAction(config,{type:'assign',"
            "repositoryIds:['repo:a','repo:b','repo:b'],target:{name:'General'}});"
            "return {draft}; })()"
        )

        general = next(
            project
            for project in result["draft"]["projects"]
            if project["id"] == "general"
        )
        self.assertEqual(general["repositories"], ["repo:a", "repo:b"])
        self.assertEqual(result["draft"]["version"], 1)

    def test_project_actions_stage_without_persisting_or_scanning(self):
        result = self.render(
            "(() => { const original={version:1,projects:[]};"
            "const staged=stageProjectManagerAction(original,{type:'assign',repositoryIds:['repo:a'],"
            "target:{name:'General'}}); return {original,staged}; })()"
        )

        self.assertEqual(result["original"]["projects"], [])
        self.assertEqual(result["staged"]["projects"][0]["name"], "General")
        self.assertEqual(result["staged"]["projects"][0]["repositories"], ["repo:a"])

    def test_project_manager_commit_saves_once_and_scans_once(self):
        result = self.render(
            "(() => { let selectionCalls=0,identityCalls=0,scanCalls=0,closed=0;"
            "const original={version:1,projects:[],fingerprint:'old'};"
            "const first=stageProjectManagerAction(original,{type:'assign',"
            "repositoryIds:['repo:a'],target:{name:'General'}});"
            "const draft=stageProjectManagerAction(first,{type:'assign',"
            "repositoryIds:['repo:b'],target:{projectId:'general'}});"
            "const choices=new Map([['/work/a',{path:'/work/a',tracked:true}],"
            "['/work/b',{path:'/work/b',tracked:true}]]);"
            "return commitProjectManagerDraft({originalPaths:[],choices,originalConfig:original,draft},{"
            "saveSelection:async paths=>{selectionCalls+=1;return {paths,workspaces:[],repositories:[]}},"
            "loadIdentity:async()=>({...original,fingerprint:'after-selection'}),"
            "saveIdentity:async(config,fingerprint)=>{identityCalls+=1;return {...config,fingerprint}},"
            "applySelectionStatus:()=>{},invalidate:()=>{},closeDrawer:()=>{closed+=1},"
            "refresh:async()=>{scanCalls+=1}}).then(outcome=>({outcome:outcome.status,selectionCalls,"
            "identityCalls,scanCalls,closed})); })()"
        )

        self.assertEqual(
            result,
            {
                "outcome": "saved",
                "selectionCalls": 1,
                "identityCalls": 1,
                "scanCalls": 1,
                "closed": 1,
            },
        )

    def test_dirty_project_manager_close_requires_confirmation(self):
        result = self.render(
            "(() => { let prompted=0,closed=0; const dirty=requestProjectManagerClose(true,"
            "()=>{prompted+=1},()=>{closed+=1}); const clean=requestProjectManagerClose(false,"
            "()=>{prompted+=1},()=>{closed+=1}); return {dirty,clean,prompted,closed}; })()"
        )

        self.assertEqual(
            result,
            {"dirty": "confirm", "clean": "closed", "prompted": 1, "closed": 1},
        )

    def test_project_manager_uses_split_layout_with_right_project_pane(self):
        script = (ROOT / "dist" / "app.js").read_text()

        self.assertIn('class="project-manager-layout"', script)
        self.assertIn('id="projectManagerPane"', script)
        self.assertIn('class="project-manager-footer"', script)

    def test_project_action_error_preserves_working_config_and_focus_helper_restores_trigger(
        self,
    ):
        result = self.render(
            "(() => { const config={version:1,projects:[{id:'old',name:'Old',repositories:[],"
            "primary_workspaces:{},workspace_aliases:[]},{id:'other',name:'Other',repositories:[],"
            "primary_workspaces:{},workspace_aliases:[]}]}; const before=JSON.stringify(config);"
            "let focused=false; document.querySelector=()=>({focus(){focused=true}});"
            "try { stageProjectManagerAction(config,{type:'rename',projectId:'old',name:'Other'}); }"
            "catch(error) {"
            "showProjectManagerError(error); restoreProjectManagerFocus('[data-rename-project=old]');"
            "return {unchanged:JSON.stringify(config)===before,error:document.getElementById('settingsError').textContent,focused}; } })()"
        )

        self.assertTrue(result["unchanged"])
        self.assertEqual(result["error"], "Project name already exists.")
        self.assertTrue(result["focused"])

    def test_api_exposes_structured_http_error(self):
        result = self.render(
            "(() => { fetch=async()=>({ok:false,status:409,json:async()=>({"
            "error:'Reload grouping',code:'identity_config_conflict'})});"
            "return api('project-identities',{version:1,projects:[]}).catch(error=>({"
            "message:error.message,status:error.status,code:error.code})); })()"
        )

        self.assertEqual(
            result,
            {
                "message": "Reload grouping",
                "status": 409,
                "code": "identity_config_conflict",
            },
        )

    def test_project_identity_save_includes_fingerprint_and_complete_document(self):
        result = self.render(
            "(() => { let body=null; fetch=async(_url,options)=>{body=JSON.parse(options.body);"
            "return {ok:true,status:200,json:async()=>({version:1,projects:body.projects,"
            "fingerprint:'new-fingerprint',warnings:[]})};};"
            "return saveProjectIdentityDraft({version:1,projects:[{id:'general',name:'General',"
            "repositories:[],primary_workspaces:{},workspace_aliases:[]}],warnings:[{code:'keep-out'}]},"
            "'old-fingerprint').then(saved=>({body,saved})); })()"
        )

        self.assertEqual(result["body"]["expected_fingerprint"], "old-fingerprint")
        self.assertEqual(result["body"]["version"], 1)
        self.assertEqual(result["body"]["projects"][0]["id"], "general")
        self.assertNotIn("warnings", result["body"])
        self.assertEqual(result["saved"]["fingerprint"], "new-fingerprint")

    def test_project_identity_conflict_reloads_and_keeps_editor_open(self):
        result = self.render(
            "(() => { const conflict=new Error('Conflict'); conflict.status=409;"
            "conflict.code='identity_config_conflict'; aiUsage={old:true};"
            "const original={version:1,projects:[],fingerprint:'stale'};"
            "const draft=stageProjectManagerAction(original,{type:'assign',"
            "repositoryIds:['repo:a'],target:{name:'General'}});"
            "const choices=new Map([['/work/a',{path:'/work/a',tracked:true}]]);"
            "let closed=0,refreshed=0; return commitProjectManagerDraft({"
            "originalPaths:['/work/a'],choices,originalConfig:original,draft},{"
            "saveIdentity:async()=>{throw conflict},loadIdentity:async()=>({version:1,"
            "projects:[{id:'newer',name:'Newer'}],fingerprint:'newer-fingerprint',warnings:[]}),"
            "closeDrawer:()=>{closed+=1},refresh:()=>{refreshed+=1}}).then(result=>({"
            "status:result.status,project:result.config.projects[0].id,closed,refreshed,"
            "usageStillLoaded:aiUsage.old===true})); })()"
        )

        self.assertEqual(
            result,
            {
                "status": "conflict",
                "project": "newer",
                "closed": 0,
                "refreshed": 0,
                "usageStillLoaded": True,
            },
        )

    def test_project_identity_save_invalidates_ai_and_reports_refresh_failure(self):
        result = self.render(
            "(() => { paired=true; paths=['/work/old']; aiUsage={totals:{input_tokens:99}};"
            "aiUsageInvalidatedByIdentity=false;"
            "statsByPath={'/work/old':{commitCount:7}}; statsProjects=[{project_id:'old',"
            "project_name:'Old grouping',commitCount:7,repositories:[]}];"
            "gitStatsInvalidatedByIdentity=false; let closed=0;"
            "const original={version:1,projects:[],fingerprint:'old'};"
            "const draft=stageProjectManagerAction(original,{type:'assign',"
            "repositoryIds:['repo:a'],target:{name:'General'}});"
            "const choices=new Map([['/work/old',{path:'/work/old',tracked:true}]]);"
            "return commitProjectManagerDraft({originalPaths:['/work/old'],choices,"
            "originalConfig:original,draft},{saveIdentity:async config=>({...config,fingerprint:'fresh'}),"
            "closeDrawer:()=>{closed+=1},refresh:()=>Promise.reject(new Error('offline'))})"
            ".then(result=>({status:result.status,closed,aiUsage,invalidated:aiUsageInvalidatedByIdentity,"
            "gitInvalidated:gitStatsInvalidatedByIdentity,statsProjects,statsByPath,"
            "numbers:portfolioNumbers(),statsPanel:(paintStats(),document.getElementById('statsView').innerHTML),"
            "message:document.getElementById('toast').textContent,panel:paintAiTokensPanel()})); })()"
        )

        self.assertEqual(result["status"], "refresh_failed")
        self.assertEqual(result["closed"], 1)
        self.assertIsNone(result["aiUsage"])
        self.assertTrue(result["invalidated"])
        self.assertTrue(result["gitInvalidated"])
        self.assertEqual(result["statsProjects"], [])
        self.assertEqual(result["statsByPath"], {})
        self.assertEqual(result["numbers"]["commits"], 0)
        self.assertNotIn("Old grouping", result["statsPanel"])
        self.assertIn("Project grouping changed", result["statsPanel"])
        self.assertIn("Changes saved; scan failed", result["message"])
        self.assertIn("Project grouping changed", result["panel"])
        self.assertIn("Aggregate", result["panel"])

    def test_tracking_scope_change_clears_all_loaded_report_state(self):
        result = self.render(
            "(() => { paths=['/work/old']; repos=[{path:'/work/old'}]; workspaces=[{workspace_id:'/work/old'}];"
            "statsByPath={'/work/old':{commitCount:7}}; statsProjects=[{project_id:'old'}];"
            "aiSessions={sessions:[{id:'old'}]}; aiSearch={results:[{id:'old'}]};"
            "aiUsage={totals:{input_tokens:9}}; aiUsagePeriodKey='2026-10';"
            "statsExpandedProjects.add('old'); statsExpandedRepositories.add('old');"
            "aiUsageExpandedProjects.add('old'); aiUsageExpandedModels.add('old');"
            "invalidateTrackedScopeData(); return {statsByPath,statsProjects,aiSessions,aiSearch,"
            "aiUsage,aiUsagePeriodKey,statsExpandedProjects:statsExpandedProjects.size,"
            "statsExpandedRepositories:statsExpandedRepositories.size,"
            "aiUsageExpandedProjects:aiUsageExpandedProjects.size,"
            "aiUsageExpandedModels:aiUsageExpandedModels.size}; })()"
        )

        self.assertEqual(
            result,
            {
                "statsByPath": {},
                "statsProjects": [],
                "aiSessions": None,
                "aiSearch": None,
                "aiUsage": None,
                "aiUsagePeriodKey": None,
                "statsExpandedProjects": 0,
                "statsExpandedRepositories": 0,
                "aiUsageExpandedProjects": 0,
                "aiUsageExpandedModels": 0,
            },
        )

    def test_successful_ai_aggregate_clears_grouping_staleness(self):
        result = self.render(
            "(() => { paired=true; aiTab='tokens'; aiUsageInvalidatedByIdentity=true;"
            "fetch=async()=>({ok:true,status:200,json:async()=>({totals:{input_tokens:3},"
            "by_date:[],by_project:[],by_model:[],by_surface:[],by_tool:[],caveats:[]})});"
            "return runAiUsage().then(()=>({invalidated:aiUsageInvalidatedByIdentity,"
            "input:aiUsage.totals.input_tokens})); })()"
        )

        self.assertEqual(result, {"invalidated": False, "input": 3})

    def test_identity_refresh_reports_busy_scan_as_retryable_failure(self):
        result = self.render(
            "(() => { paired=true; paths=['/work/a']; busy=true;"
            "return runScan({throwOnError:true}).then(()=>({message:null}),"
            "error=>({message:error.message})); })()"
        )

        self.assertEqual(result, {"message": "Another scan is already running."})

    def test_successful_scan_refreshes_workspace_identity_and_clears_git_staleness(
        self,
    ):
        result = self.render(
            "(() => { paired=true; paths=['/work/a']; gitStatsInvalidatedByIdentity=true;"
            "workspaces=[{workspace_id:'/work/a',path:'/work/a',repository_id:'repo:a',"
            "project_id:'old',project_name:'Old grouping'}]; let calls=0;"
            "fetch=async()=>{calls+=1; return {ok:true,status:200,json:async()=>calls===1?"
            "{machine:'test',scannedAt:'2026-10-11T00:00:00Z',repositories:[{workspace_id:'/work/a',"
            "path:'/work/a',name:'a',repository_id:'repo:a',project_id:'repo:a',project_name:'a',modified:0,"
            "untracked:0}]}:{repositories:[],projects:[]}}};"
            "return runScan({throwOnError:true}).then(()=>({workspaceProject:workspaces[0].project_id,"
            "invalidated:gitStatsInvalidatedByIdentity})); })()"
        )

        self.assertEqual(result, {"workspaceProject": "repo:a", "invalidated": False})

    def test_manager_does_not_fall_back_to_obsolete_project_snapshot(self):
        html = self.render(
            "projectManagerRows(new Map([['/work/a',{workspace_id:'/work/a',path:'/work/a',"
            "workspace_name:'a',repository_id:'repo:a',repository_name:'a',project_id:'old',"
            "project_name:'Old grouping',tracked:true}]]),new Set(),{version:1,projects:[]})"
        )

        self.assertNotIn("Old grouping", html)
        self.assertIn("Project: a", html)

    def test_project_save_gate_blocks_duplicate_submissions_and_reenables_controls(
        self,
    ):
        result = self.render(
            "(() => { const state={saving:false}; let calls=0,release;"
            "const pending=new Promise(resolve=>{release=resolve}); const transitions=[];"
            "const first=runProjectManagerSaveOnce(state,()=>{calls+=1; return pending},"
            "saving=>transitions.push(saving));"
            "const second=runProjectManagerSaveOnce(state,()=>{calls+=1},"
            "saving=>transitions.push(saving)); const during=state.saving; release('saved');"
            "return Promise.all([first,second]).then(values=>({calls,during,after:state.saving,"
            "transitions,values})); })()"
        )

        self.assertEqual(result["calls"], 1)
        self.assertTrue(result["during"])
        self.assertFalse(result["after"])
        self.assertEqual(result["transitions"], [True, False])
        self.assertEqual(result["values"], ["saved", {"status": "busy"}])

    def test_project_save_control_restore_keeps_protected_actions_disabled(self):
        result = self.render(
            "(() => { const active={disabled:false},protectedAction={disabled:true};"
            "document.querySelectorAll=()=>[active,protectedAction];"
            "setProjectManagerSaving(true); const during=[active.disabled,protectedAction.disabled];"
            "setProjectManagerSaving(false); return {during,after:[active.disabled,"
            "protectedAction.disabled]}; })()"
        )

        self.assertEqual(result["during"], [True, True])
        self.assertEqual(result["after"], [False, True])

    def test_identity_warning_message_distinguishes_invalid_from_empty_config(self):
        result = self.render(
            "({invalid:projectIdentityWarningsMessage({warnings:[{code:'invalid_identity_config'}]}),"
            "row:projectIdentityWarningsMessage({warnings:[{code:'workspace_missing',"
            "workspace_id:'/old/worktree'}]}),empty:projectIdentityWarningsMessage({warnings:[]})})"
        )

        self.assertIn("invalid", result["invalid"].lower())
        self.assertIn("editing is disabled", result["invalid"].lower())
        self.assertIn("workspace_missing", result["row"])
        self.assertIn("/old/worktree", result["row"])
        self.assertEqual(result["empty"], "Project grouping is ready.")

    def test_project_manager_row_wraps_distinct_accessible_controls(self):
        html = self.render(
            "projectManagerRows(new Map([['/work/a',{workspace_id:'/work/a',"
            "workspace_name:'Alpha',path:'/work/a',repository_id:'repo:a',"
            "repository_name:'alpha',project_id:'alpha',project_name:'Alpha',tracked:true}]]),"
            "new Set(['repo:a']),{version:1,projects:[]})"
        )

        self.assertRegex(
            html,
            r'class="project-row-actions"[\s\S]*aria-label="Select alpha for grouping"[\s\S]*aria-label="Track Alpha"',
        )
        self.assertIn('class="project-manager-row"', html)

    def test_project_editor_focuses_first_control_and_cancel_restores_trigger(self):
        result = self.render(
            "(() => { let firstFocused=false,triggerFocused=false;"
            "document.querySelector=(selector)=>selector==='[data-project-editor=bulk]'?"
            "{querySelector(){return {focus(){firstFocused=true}}}}:"
            "{focus(){triggerFocused=true}};"
            "focusFirstProjectEditorControl('[data-project-editor=bulk]');"
            "restoreProjectManagerFocus('#groupSelected');"
            "return {firstFocused,triggerFocused}; })()"
        )

        self.assertEqual(result, {"firstFocused": True, "triggerFocused": True})


if __name__ == "__main__":
    unittest.main()
