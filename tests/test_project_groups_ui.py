import json
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProjectGroupsUiTests(unittest.TestCase):
    def evaluate(self, expression):
        harness = r"""
const fs = require('fs');
const vm = require('vm');
const context = {JSON, Set, Map};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('dist/project_groups.js', 'utf8'), context);
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

    def test_slug_and_project_name_validation(self):
        result = self.evaluate(
            "(() => { const projects=[{id:'general',name:'General'},"
            "{id:'general-2',name:'Alpha'}]; return {"
            "trimmed:slugProjectId('  General Tools  ', []),"
            "unicode:slugProjectId('Dự án Tổng quát', []),"
            "collision:slugProjectId('General', projects),"
            "empty:projectNameError(projects, '   '),"
            "duplicate:projectNameError(projects, ' alpha '),"
            "same:projectNameError(projects, ' alpha ', 'general-2')}; })()"
        )

        self.assertEqual(
            result,
            {
                "trimmed": "general-tools",
                "unicode": "du-an-tong-quat",
                "collision": "general-3",
                "empty": "Project name is required.",
                "duplicate": "Project name already exists.",
                "same": None,
            },
        )

    def test_assign_repositories_moves_membership_and_matching_primary(self):
        result = self.evaluate(
            "(() => { const input={version:1,projects:["
            "{id:'one',name:'One',repositories:['repo:one','repo:hidden'],"
            "primary_workspaces:{'repo:one':'/work/one','repo:hidden':'/work/hidden'},"
            "workspace_aliases:['/old/one'],metadata:{keep:true}},"
            "{id:'two',name:'Two',repositories:['repo:two'],primary_workspaces:{},"
            "workspace_aliases:[]} ]}; const before=JSON.stringify(input);"
            "const output=assignRepositories(input,['repo:one','repo:two','repo:two'],"
            "{name:'General'}); return {beforeUnchanged:JSON.stringify(input)===before,"
            "projectId:output.projectId,config:output.config}; })()"
        )

        self.assertTrue(result["beforeUnchanged"])
        self.assertEqual(result["projectId"], "general")
        projects = {project["id"]: project for project in result["config"]["projects"]}
        self.assertEqual(projects["one"]["repositories"], ["repo:hidden"])
        self.assertEqual(
            projects["one"]["primary_workspaces"],
            {"repo:hidden": "/work/hidden"},
        )
        self.assertEqual(projects["one"]["workspace_aliases"], ["/old/one"])
        self.assertEqual(projects["one"]["metadata"], {"keep": True})
        self.assertEqual(projects["two"]["repositories"], [])
        self.assertEqual(projects["general"]["repositories"], ["repo:one", "repo:two"])
        self.assertEqual(
            projects["general"]["primary_workspaces"], {"repo:one": "/work/one"}
        )

    def test_standalone_removes_selected_membership_and_prunes_only_plain_empty_project(
        self,
    ):
        result = self.evaluate(
            "(() => { const input={version:1,projects:["
            "{id:'plain',name:'Plain',repositories:['repo:one'],primary_workspaces:{},workspace_aliases:[]},"
            "{id:'history',name:'History',repositories:['repo:two','repo:hidden'],"
            "primary_workspaces:{'repo:two':'/work/two'},workspace_aliases:['/old/two']} ]};"
            "const before=JSON.stringify(input); const output=makeRepositoriesStandalone("
            "input,['repo:one','repo:two','repo:two']); return {beforeUnchanged:JSON.stringify(input)===before,output}; })()"
        )

        self.assertTrue(result["beforeUnchanged"])
        self.assertEqual(
            [project["id"] for project in result["output"]["projects"]], ["history"]
        )
        history = result["output"]["projects"][0]
        self.assertEqual(history["repositories"], ["repo:hidden"])
        self.assertEqual(history["primary_workspaces"], {})
        self.assertEqual(history["workspace_aliases"], ["/old/two"])

    def test_rename_preserves_identity_fields_and_delete_protects_advanced_mappings(
        self,
    ):
        result = self.evaluate(
            "(() => { const input={version:1,projects:["
            "{id:'alpha',name:'Alpha',repositories:['repo:a'],"
            "primary_workspaces:{'repo:a':'/work/a'},workspace_aliases:['/old/a']},"
            "{id:'plain',name:'Plain',repositories:['repo:p'],primary_workspaces:{},workspace_aliases:[]} ]};"
            "const renamed=renameConfiguredProject(input,'alpha',' Alpha Prime ');"
            "let protectedMessage=''; try { deleteConfiguredProject(input,'alpha'); }"
            "catch(error) { protectedMessage=error.message; }"
            "const deleted=deleteConfiguredProject(input,'plain');"
            "return {renamed:renamed.projects[0],protectedMessage,deletedIds:deleted.projects.map(p=>p.id),"
            "inputName:input.projects[0].name}; })()"
        )

        self.assertEqual(result["renamed"]["id"], "alpha")
        self.assertEqual(result["renamed"]["name"], "Alpha Prime")
        self.assertEqual(result["renamed"]["repositories"], ["repo:a"])
        self.assertEqual(result["renamed"]["primary_workspaces"], {"repo:a": "/work/a"})
        self.assertEqual(result["renamed"]["workspace_aliases"], ["/old/a"])
        self.assertIn("historical", result["protectedMessage"].lower())
        self.assertEqual(result["deletedIds"], ["alpha"])
        self.assertEqual(result["inputName"], "Alpha")


if __name__ == "__main__":
    unittest.main()
