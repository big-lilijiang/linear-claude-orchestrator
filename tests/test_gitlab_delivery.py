import unittest

from damon_autocoding.gitlab import GitLabDelivery, MergeRequestSpec
from damon_autocoding.project import DeliveryOptions, GitLabProject, ProjectConfig


class GitLabDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ProjectConfig(
            version="0.1",
            name="Damon AutoCoding",
            remote_name="origin",
            remote_url="git@gitlab.kidinsight.cn:autocoding/test.git",
            default_target_branch="main",
            gitlab=GitLabProject(
                api_base_url="https://gitlab.kidinsight.cn/api/v4",
                web_base_url="https://gitlab.kidinsight.cn",
                project_path="autocoding/test",
            ),
            delivery=DeliveryOptions(
                use_push_options=True,
                draft_merge_requests=True,
                default_labels=["damon", "bootstrap"],
            ),
        )

    def test_build_push_command_uses_merge_request_options(self) -> None:
        spec = MergeRequestSpec(
            source_branch="damon/bootstrap-control-plane",
            target_branch="main",
            title="Bootstrap Damon AutoCoding control plane",
            description="Automated delivery by Damon AutoCoding.",
            draft=True,
            labels=["damon", "bootstrap"],
        )
        command = GitLabDelivery(self.project).build_push_command(spec)
        self.assertIn("merge_request.create", command)
        self.assertIn("merge_request.target=main", command)
        self.assertIn("merge_request.title=Draft: Bootstrap Damon AutoCoding control plane", command)
        self.assertIn("merge_request.label=damon,bootstrap", command)

    def test_build_api_payload_encodes_labels(self) -> None:
        spec = MergeRequestSpec(
            source_branch="feature",
            target_branch="main",
            title="Add API delivery",
            description="Description",
            draft=False,
            labels=["damon"],
        )
        payload = GitLabDelivery(self.project).build_api_payload(spec)
        self.assertEqual(payload["source_branch"], "feature")
        self.assertEqual(payload["labels"], "damon")


if __name__ == "__main__":
    unittest.main()
