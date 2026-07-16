import unittest

from local_runtime import LocalAgentRuntime


class LocalAgentRuntimeTest(unittest.TestCase):
    def test_send_message(self):
        runtime = LocalAgentRuntime()

        response, events = runtime.execute(
            "recon",
            {
                "schema_version": "1.0",
                "workflow_id": "wf-local",
                "work_item": "wf-local:recon",
                "command": "scan_beach_defenses",
                "required_skill": "scan_beach_defenses",
                "input": {"sector": "Sector_A"},
                "output_hint": "recon_report",
            },
            stream=False,
        )

        self.assertEqual(response["mode"], "local")
        self.assertEqual(response["role"], "recon")
        self.assertEqual(events, [])

    def test_stream_artillery(self):
        runtime = LocalAgentRuntime()

        response, events = runtime.execute(
            "artillery",
            {
                "schema_version": "1.0",
                "workflow_id": "wf-local",
                "work_item": "wf-local:artillery",
                "command": "suppress_beach_sector_A",
                "required_skill": "suppress_beach_sector_A",
                "input": {"coordinates": "120.5E, 35.1N"},
                "output_hint": "strike_result",
            },
            stream=True,
        )

        self.assertEqual(response["mode"], "local")
        self.assertEqual(events[-1]["status"], "Completed")
        self.assertEqual(events[-1]["progress"], "100%")


if __name__ == "__main__":
    unittest.main()
