"""Registering actors (lab record spec v0.19 §5.2; rule L28; verify notes)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from claimset.lab.core import Lab, LabError, verify

from test_lab import TestCli, ticking_clock

MODEL = [{"provider": "Anthropic", "name": "Claude Opus", "version": "5.5"}]
WORDS = "I'm Jane Doe, ORCID 0000-0002-1825-0097."


class ActorCase(unittest.TestCase):
    """A lab begun under v0.19 where the agent registers itself first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Lab.create(Path(self.tmp.name) / "lab", "Study", clock=ticking_clock())
        self.agent = self.lab.append("actor", {"kind": "agent", "name": "Agent", "models": MODEL}, by="AC1")["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def actor(self, body, by):
        return self.lab.append("actor", body, by=by)

    def refused(self, body, by, supersedes=None):
        with self.assertRaises(LabError) as cm:
            self.lab.append("actor", body, by=by, supersedes=supersedes)
        self.assertEqual(cm.exception.rule, "L28", cm.exception.message)
        return cm.exception.message

    def notes(self):
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])
        return " | ".join(rep["notes"])


class TestRegistration(ActorCase):
    def test_an_agent_registers_a_person_with_their_words(self):
        ev = self.actor({"kind": "human", "name": "Jane Doe", "orcid": "0000-0002-1825-0097", "words": WORDS,
                         "given_at": "2026-11-05T20:30:00Z", "channel": "chat"}, by=self.agent)
        self.assertEqual((ev["by"], ev["body"]["words"]), (self.agent, WORDS))
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])

    def test_a_person_registers_themselves_and_others(self):
        jane = self.actor({"kind": "human", "name": "Jane Doe"}, by="AC2")["id"]
        self.actor({"kind": "human", "name": "John Roe"}, by=jane)
        self.actor({"kind": "agent", "name": "Second agent", "models": MODEL}, by=jane)
        self.actor({"kind": "organisation", "name": "A lab"}, by=self.agent)        # no one to quote
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])

    def test_L28_an_agent_never_registers_a_person_without_their_words(self):
        msg = self.refused({"kind": "human", "name": "Jane Doe"}, by=self.agent)
        self.assertIn("--words", msg)
        self.refused({"kind": "human", "name": "Jane Doe", "words": WORDS}, by="AC2")   # self, with words
        jane = self.actor({"kind": "human", "name": "Jane Doe", "words": WORDS}, by=self.agent)["id"]
        self.refused({"kind": "human", "name": "John Roe", "words": "I'm John."}, by=jane)  # a person writes
        self.refused({"kind": "human", "name": "Jane A. Doe"}, by=self.agent, supersedes=jane)
        self.actor({"kind": "human", "name": "Jane A. Doe"}, by=jane)                    # their own correction
        with self.assertRaises(LabError):
            self.lab.append("actor", {"kind": "human", "name": "J", "words": WORDS, "given_at": "yesterday"},
                            by=self.agent)

    def test_a_person_registered_by_the_agent_approves_through_it(self):
        jane = self.actor({"kind": "human", "name": "Jane Doe", "words": WORDS}, by=self.agent)["id"]
        q = self.lab.append("question", {"text": "Q?"}, by=self.agent)["id"]
        h = self.lab.append("hypothesis", {"statement": "X improves Y.", "question": q, "origin": "a_priori"},
                            by=self.agent)["id"]
        pl = self.lab.append("plan", {"kind": "exploratory", "hypotheses": [h], "design": "d", "analysis": "a",
                                      "requires_approval": True}, by=self.agent)["id"]
        self.lab.append("approval", {"target": pl, "on_behalf_of": jane, "words": "Approved."}, by=self.agent)
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])


class TestNotes(ActorCase):
    def test_an_agent_alone_is_noted_until_a_person_is_registered(self):
        self.assertIn("the record has no human actor", self.notes())
        self.actor({"kind": "human", "name": "Jane Doe", "words": WORDS}, by=self.agent)
        self.assertNotIn("no human actor", self.notes())

    def test_records_begun_before_v0_19_keep_their_registrations(self):
        old = Lab(Path(self.tmp.name) / "old", clock=ticking_clock())
        old.root.mkdir()
        old.append("init", {"lab_spec_version": "0.18", "working_title": "t"}, by=None)
        hum = old.append("actor", {"kind": "human", "name": "H"}, by="AC1")["id"]
        agent = old.append("actor", {"kind": "agent", "name": "A", "models": MODEL}, by=hum)["id"]
        old.append("actor", {"kind": "human", "name": "K"}, by=agent)                    # no words: allowed then
        rep = verify(old)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertIn("human actors AC1 registered themselves and then the agent", " | ".join(rep["notes"]))


class TestActorCli(TestCli):
    def test_the_agent_registers_itself_then_the_person(self):
        self.run_cli("lab", "init", "--title", "Study")
        code, out, err = self.run_cli("lab", "actor", "--kind", "agent", "--name", "A", "--model",
                                      "Anthropic|Claude Opus|5.5|claude-opus-5-5")
        self.assertEqual((code, out), (0, "AC1"), err)
        code, _, err = self.run_cli("lab", "actor", "--kind", "human", "--name", "Jane Doe", "--by", "AC1")
        self.assertNotEqual(code, 0)
        self.assertIn("--words", err)
        code, _, err = self.run_cli("lab", "actor", "--kind", "human", "--name", "Jane Doe", "--words", WORDS)
        self.assertNotEqual(code, 0)                                             # words, but no writer
        code, out, err = self.run_cli("lab", "actor", "--kind", "human", "--name", "Jane Doe", "--orcid",
                                      "0000-0002-1825-0097", "--words", WORDS, "--given-at", "2026-11-05T20:30:00Z",
                                      "--channel", "chat", "--by", "AC1")
        self.assertEqual((code, out), (0, "AC2"), err)
        ev = json.loads((Path(self.env["CLAIMSET_LAB"]) / "lab.jsonl").read_text().splitlines()[-1])
        self.assertEqual((ev["by"], ev["body"]["words"], ev["body"]["channel"]), ("AC1", WORDS, "chat"))


if __name__ == "__main__":
    unittest.main()
