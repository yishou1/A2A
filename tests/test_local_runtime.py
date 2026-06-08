import unittest

from local_runtime import LocalAgentRuntime


class LocalAgentRuntimeTest(unittest.TestCase):
    def test_send_message(self):
        runtime = LocalAgentRuntime()

        response, events = runtime.execute(
            "recon",
            {"command": "scan_beach_defenses", "sector": "Sector_A"},
            stream=False,
        )

        self.assertEqual(response["mode"], "local")
        self.assertEqual(response["role"], "recon")
        self.assertEqual(events, [])

    def test_stream_artillery(self):
        runtime = LocalAgentRuntime()

        response, events = runtime.execute(
            "artillery",
            {"command": "suppress_beach_sector_A"},
            stream=True,
        )

        self.assertEqual(response["mode"], "local")
        self.assertEqual(events[-1]["status"], "Completed")
        self.assertEqual(events[-1]["progress"], "100%")


if __name__ == "__main__":
    unittest.main()
