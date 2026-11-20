Example A (synthetic)
Evidence: rec_type video_idea; topic "teeth whitening cost"; f1 "4 of 6 competitor outliers in 30 days answer a cost question" numbers [4, 6, 30 days] supports why; f2 "Cost videos reached 3.6x their channel baseline at day 7" numbers [3.6x] supports why; f3 "Buyer question q2 'How much does whitening cost?' has no video on your channel" supports fit.
Good output:
{"summary": "Make a short explainer answering how much teeth whitening costs at your clinic", "working_title": "What Teeth Whitening Really Costs (and What Changes the Price)", "why_it_works": [{"fact_refs": ["f1"], "text": "4 of 6 competitor outliers in the last 30 days answer a cost question"}, {"fact_refs": ["f2"], "text": "Cost videos reached 3.6x their channel baseline at day 7"}], "fit_to_client": {"fact_refs": ["f3"], "text": "Your buyers ask \"How much does whitening cost?\" and your channel has no video answering it yet"}}

Example B (synthetic)
Evidence: rec_type title_pattern; topic "used SUV buying"; f1 "Titles with a question mark were in 5 of 7 outliers" numbers [5, 7] supports why; f2 "The pattern appears across 3 channels" numbers [3] supports why; f3 "Your question-style titles averaged 2.1x your baseline" numbers [2.1x] supports why and fit.
Good output:
{"summary": "Phrase used SUV buying titles as the buyer's own question", "working_title": null, "why_it_works": [{"fact_refs": ["f1"], "text": "Titles with a question mark were in 5 of 7 recent outliers"}, {"fact_refs": ["f2"], "text": "The pattern holds across 3 channels, not just one"}], "fit_to_client": {"fact_refs": ["f3"], "text": "Your own question-style titles already averaged 2.1x your baseline"}}

Bad output (never do this): "This viral idea will get 25K views, like Dr. Smith's channel" because it promises, invents a number and names a channel that is not in the pack.
