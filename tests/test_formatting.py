import unittest

from formatting import find_utf16_cut, markdown_to_chunks, split_plain_text, utf16_len


class TestFormatting(unittest.TestCase):
    def test_utf16_len(self):
        # ASCII: 1 символ = 1 unit
        self.assertEqual(utf16_len("hello"), 5)
        # Кириллица: 1 символ = 1 unit (BMP)
        self.assertEqual(utf16_len("привет"), 6)
        # Суррогатная пара (эмодзи вне BMP): 1 символ = 2 units
        self.assertEqual(utf16_len("👋"), 2)
        self.assertEqual(utf16_len("🚀🔥"), 4)
        # Смешанный текст: "Бот " (4) + "🤖" (2) + " готов!" (7) = 13
        self.assertEqual(utf16_len("Бот 🤖 готов!"), 13)

    def test_find_utf16_cut(self):
        text = "Hello 🚀 World 🌍"
        # utf16_len: "Hello " (6) + "🚀" (2) = 8
        cut = find_utf16_cut(text, 8)
        self.assertLessEqual(utf16_len(text[:cut]), 8)

    def test_split_plain_text_short(self):
        text = "Краткий текст"
        chunks = split_plain_text(text, limit=100)
        self.assertEqual(chunks, ["Краткий текст"])

    def test_split_plain_text_long(self):
        line1 = "Первая строка сообщения."
        line2 = "Вторая строка сообщения."
        text = f"{line1}\n{line2}"
        limit = utf16_len(line1) + 2
        chunks = split_plain_text(text, limit=limit)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], line1)
        self.assertEqual(chunks[1], line2)

    def test_markdown_to_chunks(self):
        md_text = "**Жирный текст** и `код`"
        chunks = markdown_to_chunks(md_text, max_len=100)
        self.assertGreater(len(chunks), 0)
        plain_text, entities = chunks[0]
        self.assertIn("Жирный текст", plain_text)
        self.assertIn("код", plain_text)
        self.assertGreater(len(entities), 0)


if __name__ == "__main__":
    unittest.main()
