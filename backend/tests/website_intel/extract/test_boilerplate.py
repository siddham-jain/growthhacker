from app.services.website_intel.extract.boilerplate import CrossPageBoilerplateRemover


def test_blocks_on_most_pages_are_removed():
    pages = [f"Book Appointment\n## Page {i}\nUnique body {i}\nCall Us" for i in range(10)]
    cleaned = CrossPageBoilerplateRemover().remove(pages)
    assert cleaned[3] == "## Page 3\nUnique body 3"


def test_fewer_than_eight_pages_are_untouched():
    pages = ["Shared\nA", "Shared\nB", "Shared\nC"]
    assert CrossPageBoilerplateRemover().remove(pages) == pages


def test_threshold_is_forty_percent_with_minimum_five_pages():
    pages = [f"body {i}" for i in range(20)]
    for i in range(7):
        pages[i] += "\nSeven times"
    for i in range(8):
        pages[i] += "\nEight times"
    cleaned = CrossPageBoilerplateRemover().remove(pages)
    assert "Seven times" in cleaned[0]
    assert "Eight times" not in cleaned[0]


def test_matching_ignores_case_markup_and_edge_punctuation():
    pages = [f"* Contact Us!\nbody {i}" for i in range(5)] + [f"contact us\nbody x{i}" for i in range(5)]
    cleaned = CrossPageBoilerplateRemover().remove(pages)
    assert all("ontact" not in text for text in cleaned)
