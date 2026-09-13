# Final comparison QA

Six figures and all 22 panels were visually inspected at the final rendered layout. Alignment, text and collision outputs accompany the editable PDFs. Minimum text size is 7 pt; zero collision/alignment failures or warnings. The only source preflight warning concerns TIFF, which is not required for this internal report. PDF, SVG and 600 dpi PNG are supplied.

The original analysis metadata correctly records QA as pending at export time. `qa.json` resolves that pending step without changing the frozen analysis files. `statistics/verification.json` and its saved source reproduce the independent daily-row, nonoverlap and paired-bootstrap checks. These checks do not change C16/C17 effect failures or make the exposed later period a new test set.
