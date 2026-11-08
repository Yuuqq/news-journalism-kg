# -*- coding: utf-8 -*-
import unittest

from wiki_match import (
    en_name_in_labels,
    identity_match,
    lifespan_ok,
    rank_person_titles,
    reject_person_title,
    titles_compatible,
    zh_name_in_labels,
    zh_title_exact,
    year_status,
)


class TitleTests(unittest.TestCase):
    def test_subtitle_still_matches_short_wiki_title(self):
        query = "The Filter Bubble: What the Internet Is Hiding from You"
        self.assertTrue(titles_compatible(query, "The Filter Bubble"))

    def test_awards_page_is_not_the_book(self):
        self.assertFalse(titles_compatible("The People's Choice", "People's Choice Awards"))

    def test_film_parenthetical_rejected(self):
        self.assertFalse(titles_compatible("Nineteen Eighty-Four", "Nineteen Eighty-Four (film)"))

    def test_groupthink_short_title(self):
        self.assertTrue(titles_compatible("Victims of Groupthink", "Groupthink"))

    def test_zh_exact_ignores_parenthetical(self):
        self.assertTrue(zh_title_exact("新闻学", "新闻学（书籍）"))
        self.assertFalse(zh_title_exact("新闻学", "新闻学概论"))


class PersonTests(unittest.TestCase):
    def test_rejects_school_fiction_and_campaign_pages(self):
        self.assertTrue(reject_person_title("Ida B. Wells-Barnett High School"))
        self.assertTrue(reject_person_title("David Manning (fictitious writer)"))
        self.assertTrue(reject_person_title("Public relations campaigns of Edward Bernays"))

    def test_does_not_accept_near_homonym(self):
        self.assertFalse(en_name_in_labels("Ge Gongzhen", ["Ge Hongsheng"]))
        self.assertFalse(en_name_in_labels("Li Liangrong", ["Li Lingrong"]))
        self.assertFalse(en_name_in_labels("David Manning White", ["David Manning"]))
        self.assertFalse(en_name_in_labels("Peng Lan", ["Penglang"]))

    def test_accepts_wade_giles_via_chinese_label(self):
        self.assertTrue(identity_match("Yu Youren", "于右任", ["Yu Yu-jen"], ["于右任"]))

    def test_accepts_initial_in_english_label(self):
        self.assertTrue(en_name_in_labels("Arthur Page", ["Arthur W. Page"]))

    def test_chinese_name_inside_longer_label(self):
        self.assertTrue(zh_name_in_labels("桑斯坦", ["凯斯·桑斯坦"]))
        self.assertFalse(zh_name_in_labels("帕里泽", ["帕里斯"]))

    def test_traditional_label_matches_simplified_name(self):
        self.assertTrue(zh_name_in_labels("黄天鹏", ["黃天鵬"]))

    def test_bennett_junior_was_not_yet_born(self):
        self.assertFalse(lifespan_ok("1841", "1835"))
        self.assertTrue(lifespan_ok("1795", "1835"))

    def test_opensearch_does_not_fall_back_to_first_hit(self):
        ranked = rank_person_titles("Ge Gongzhen", ["Ge Hongsheng", "Ge Gongzhen"])
        self.assertEqual(ranked, ["Ge Gongzhen"])
        self.assertEqual(rank_person_titles("Ge Gongzhen", ["Ge Hongsheng"]), [])


class YearTests(unittest.TestCase):
    def test_near_match_window(self):
        self.assertEqual(year_status("1944", "1947"), "mismatch")
        self.assertEqual(year_status("1944", "1945"), "near_match")
        self.assertEqual(year_status("1962", "1962"), "verified")
        self.assertEqual(year_status("1962", ""), "no_year_found")


if __name__ == "__main__":
    unittest.main()
