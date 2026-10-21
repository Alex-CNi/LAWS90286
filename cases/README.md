# cases/ — the case database committed with the app (optional)

If a file `cases/database.json` is here, every user of the app starts with its cases on the **Case database**
page, and the estimate page uses them as comparable cases (yardsticks of current sentencing practice — never a
source of law; the Manual is the only source of law).

To make one:

1. Run the app, open **Case database**, upload your documents of case summaries (for example the Judicial
   College's *Sentencing Manual case summaries*, downloaded as Word, PDF, text or a saved web page) and press
   **Chunk and label the cases with AI**. Check the labels and figures in the table.
2. Press **Download the database (.json)**.
3. Save the file here as `cases/database.json`, commit and push. The deployed app loads it on its next start.

The file holds each case's text (word for word from your document), its labels and figures, and its embedding
(text-embedding-3-large, 1024 dimensions). It contains no API key.

Case summaries published by the Judicial College of Victoria are its material: check its terms of use before
committing them to a public repository (a private repository shared with your marker avoids publishing them).
