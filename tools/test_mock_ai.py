import json
import unittest

from mock_ai import completion, embeddings


class MockAiTest(unittest.TestCase):
    def test_embeddings_keep_input_order(self):
        self.assertEqual(
            embeddings({"input": ["первый", "second"]}),
            {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [1.0, 0.0]}]},
        )

    def test_sample_answer_and_insufficient_response(self):
        def request(question):
            return {"messages": [{"content": json.dumps({"question": question, "sources": [
                {"id": "c1", "excerpt": "Срок подачи заявки на проект «Лазурь» — 12 мая 2027 года."},
            ]})}]}

        supported = json.loads(completion(request("Какой срок заявки проекта Лазурь?"))["choices"][0]["message"]["content"])
        unsupported = json.loads(completion(request("Каков бюджет проекта Лазурь?"))["choices"][0]["message"]["content"])
        self.assertEqual(supported["citation_ids"], ["c1"])
        self.assertEqual(unsupported, {"insufficient": True, "citation_ids": []})

    def test_english_sample_answer(self):
        request = {"messages": [{"content": json.dumps({
            "question": "What is the opening day of the Project Atlas help desk?",
            "sources": [{"id": "c2", "excerpt": "The Project Atlas help desk is open on Tuesdays."}],
        })}]}
        answer = json.loads(completion(request)["choices"][0]["message"]["content"])
        self.assertEqual(answer["citation_ids"], ["c2"])
        self.assertIn("Tuesdays", answer["answer"])


if __name__ == "__main__":
    unittest.main()
