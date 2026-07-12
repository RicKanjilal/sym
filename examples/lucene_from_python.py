"""Apache Lucene — Java's legendary search engine — from plain Python.
Once: sym add java:lucene            Then: python3 lucene_from_python.py"""
import sym

lucene = sym.java.package("org.apache.lucene")

mem    = lucene.store.ByteBuffersDirectory()
writer = lucene.index.IndexWriter(
             mem, lucene.index.IndexWriterConfig(
                      lucene.analysis.standard.StandardAnalyzer()))

for text in ["sym is the polyglot host",
             "nothing to see here",
             "ric built sym at fifteen"]:
    doc = lucene.document.Document()
    doc.add(lucene.document.TextField("body", text,
                                      lucene.document.Field.Store.YES))
    writer.addDocument(doc)
writer.close()

searcher = lucene.search.IndexSearcher(lucene.index.DirectoryReader.open(mem))
hits = searcher.search(
    lucene.search.TermQuery(lucene.index.Term("body", "sym")), 10)

print("matches:", hits.totalHits().value())
stored = searcher.storedFields()
for sd in hits.scoreDocs():
    print("  hit:", stored.document(sd.doc()).get("body"))
