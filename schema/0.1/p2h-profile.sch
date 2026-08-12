<?xml version="1.0" encoding="UTF-8"?>
<schema xmlns="http://purl.oclc.org/dsdl/schematron"
        xmlns:xlink="http://www.w3.org/1999/xlink"
        queryBinding="xslt2"
        defaultPhase="full"
        schemaVersion="0.1">
  <title>Paper2HTML XML Profile 0.1</title>
  <ns prefix="xlink" uri="http://www.w3.org/1999/xlink"/>

  <phase id="full">
    <active pattern="document-root"/>
    <active pattern="addressability"/>
    <active pattern="identifier-prefixes"/>
    <active pattern="formulae"/>
    <active pattern="figures-and-tables"/>
    <active pattern="cross-references"/>
    <active pattern="resource-paths"/>
  </phase>

  <pattern id="document-root">
    <rule context="/*">
      <assert id="p2h-root-name" role="error" test="self::article or self::book">
        The document root must be an unnamespaced JATS article or BITS book element.
      </assert>
      <assert id="p2h-no-default-namespace" role="error" test="namespace-uri(.) = ''">
        P2H 0.1 uses the unnamespaced JATS/BITS vocabulary.
      </assert>
      <assert id="p2h-document-id" role="error" test="matches(@id, '^doc-[0-9]{6}$')">
        The document root must have an id matching doc-NNNNNN.
      </assert>
      <assert id="p2h-document-language" role="error" test="normalize-space(@xml:lang) != ''">
        The document root must declare xml:lang.
      </assert>
      <assert id="p2h-jats-version" role="error" test="not(self::article) or @dtd-version = '1.3'">
        A P2H article must declare dtd-version="1.3".
      </assert>
      <assert id="p2h-article-structure" role="error" test="not(self::article) or (front and body and back)">
        A P2H article must contain front, body, and back.
      </assert>
      <assert id="p2h-bits-version" role="error" test="not(self::book) or @dtd-version = '2.1'">
        A P2H book must declare dtd-version="2.1".
      </assert>
      <assert id="p2h-book-structure" role="error" test="not(self::book) or (book-meta and book-body)">
        A P2H book must contain book-meta and book-body.
      </assert>
    </rule>
  </pattern>

  <pattern id="addressability">
    <rule context="*[@id]">
      <assert id="p2h-id-lexical-form" role="error"
              test="matches(@id, '^[a-z][a-z0-9]*(-[a-z0-9]+)*$')">
        Every id must use the P2H lowercase hyphenated identifier syntax.
      </assert>
      <assert id="p2h-id-unique" role="error" test="count(//*[@id = current()/@id]) = 1">
        Every id must be globally unique in document.xml.
      </assert>
    </rule>
    <rule context="article-title | subtitle | title | book-title | book-subtitle | journal-title | journal-subtitle | article-id | book-id | journal-id | isbn | issn | contrib | name | collab | aff | pub-date | abstract | kwd | funding-source | license-p | copyright-statement | book-part | sec | p | list | list-item | disp-formula | inline-formula | fig | caption | table-wrap | td | th | fn | ref | boxed-text | preformat | supplementary-material">
      <assert id="p2h-addressable-element-id" role="error" test="@id">
        Every P2H-addressable content or visible metadata element must have an id.
      </assert>
    </rule>
  </pattern>

  <pattern id="identifier-prefixes">
    <rule context="book-part"><assert role="error" test="matches(@id, '^part-[0-9]{4}$')">book-part ids must match part-NNNN.</assert></rule>
    <rule context="sec"><assert role="error" test="matches(@id, '^sec-[0-9]{6}$')">sec ids must match sec-NNNNNN.</assert></rule>
    <rule context="article-title | subtitle | title | book-title | book-subtitle | journal-title | journal-subtitle"><assert role="error" test="matches(@id, '^title-[0-9]{6}$')">Title ids must match title-NNNNNN.</assert></rule>
    <rule context="p"><assert role="error" test="matches(@id, '^p-[0-9]{6}$')">Paragraph ids must match p-NNNNNN.</assert></rule>
    <rule context="list"><assert role="error" test="matches(@id, '^list-[0-9]{6}$')">List ids must match list-NNNNNN.</assert></rule>
    <rule context="list-item"><assert role="error" test="matches(@id, '^li-[0-9]{6}$')">List item ids must match li-NNNNNN.</assert></rule>
    <rule context="disp-formula"><assert role="error" test="matches(@id, '^eq-[0-9]{6}$')">Display formula ids must match eq-NNNNNN.</assert></rule>
    <rule context="inline-formula"><assert role="error" test="matches(@id, '^ineq-[0-9]{6}$')">Inline formula ids must match ineq-NNNNNN.</assert></rule>
    <rule context="fig"><assert role="error" test="matches(@id, '^fig-[0-9]{6}$')">Figure ids must match fig-NNNNNN.</assert></rule>
    <rule context="caption"><assert role="error" test="matches(@id, '^caption-[0-9]{6}$')">Caption ids must match caption-NNNNNN.</assert></rule>
    <rule context="table-wrap"><assert role="error" test="matches(@id, '^tbl-[0-9]{6}$')">Table ids must match tbl-NNNNNN.</assert></rule>
    <rule context="td | th"><assert role="error" test="matches(@id, '^cell-[0-9]{6}$')">Table cell ids must match cell-NNNNNN.</assert></rule>
    <rule context="fn"><assert role="error" test="matches(@id, '^fn-[0-9]{6}$')">Footnote ids must match fn-NNNNNN.</assert></rule>
    <rule context="ref"><assert role="error" test="matches(@id, '^ref-[0-9]{6}$')">Reference ids must match ref-NNNNNN.</assert></rule>
    <rule context="supplementary-material"><assert role="error" test="matches(@id, '^supp-[0-9]{6}$')">Supplement ids must match supp-NNNNNN.</assert></rule>
    <rule context="boxed-text"><assert role="error" test="matches(@id, '^quote-[0-9]{6}$')">Quoted block ids must match quote-NNNNNN.</assert></rule>
    <rule context="preformat"><assert role="error" test="matches(@id, '^code-[0-9]{6}$')">Preformatted code ids must match code-NNNNNN.</assert></rule>
  </pattern>

  <pattern id="formulae">
    <rule context="inline-formula | disp-formula">
      <assert id="p2h-formula-tex" role="error" test="count(tex-math) = 1 and normalize-space(tex-math) != ''">
        Every formula must contain exactly one non-empty tex-math child.
      </assert>
      <assert id="p2h-formula-no-outer-delimiters" role="error"
              test="not(starts-with(normalize-space(tex-math), '$')) and not(starts-with(normalize-space(tex-math), '\(')) and not(starts-with(normalize-space(tex-math), '\[')) and not(ends-with(normalize-space(tex-math), '$')) and not(ends-with(normalize-space(tex-math), '\)')) and not(ends-with(normalize-space(tex-math), '\]'))">
        tex-math must not include outer TeX math delimiters.
      </assert>
    </rule>
  </pattern>

  <pattern id="figures-and-tables">
    <rule context="fig">
      <assert id="p2h-figure-graphic" role="error" test="graphic">Every figure must contain a graphic.</assert>
      <assert id="p2h-figure-caption" role="error" test="caption">Every figure must contain a structured caption.</assert>
    </rule>
    <rule context="table-wrap[not(@specific-use = 'image-only')]">
      <assert id="p2h-structured-table" role="error" test="table and table//tr and table//*[self::td or self::th]">
        A structured table-wrap must contain table rows and cells.
      </assert>
    </rule>
    <rule context="table-wrap[@specific-use = 'image-only']">
      <assert id="p2h-image-table-graphic" role="error" test="graphic">An image-only table must contain a graphic.</assert>
      <report id="p2h-image-table-warning" role="warning" test="true()">The table is represented by the permitted image-only fallback.</report>
    </rule>
  </pattern>

  <pattern id="cross-references">
    <rule context="xref">
      <assert id="p2h-xref-rid" role="error" test="normalize-space(@rid) != ''">Every xref must have a non-empty rid.</assert>
      <assert id="p2h-xref-target-exists" role="error"
              test="every $target-id in tokenize(normalize-space(@rid), '\s+') satisfies exists(//*[@id = $target-id])">
        Every whitespace-separated token in xref/@rid must identify an existing element.
      </assert>
      <assert role="error" test="not(@ref-type = 'fig') or (every $i in tokenize(@rid, '\s+') satisfies exists(//fig[@id = $i]))">fig xrefs must target fig elements.</assert>
      <assert role="error" test="not(@ref-type = 'table') or (every $i in tokenize(@rid, '\s+') satisfies exists(//table-wrap[@id = $i]))">table xrefs must target table-wrap elements.</assert>
      <assert role="error" test="not(@ref-type = 'disp-formula') or (every $i in tokenize(@rid, '\s+') satisfies exists(//disp-formula[@id = $i]))">disp-formula xrefs must target display formulas.</assert>
      <assert role="error" test="not(@ref-type = 'fn') or (every $i in tokenize(@rid, '\s+') satisfies exists(//fn[@id = $i]))">fn xrefs must target footnotes.</assert>
      <assert role="error" test="not(@ref-type = 'bibr') or (every $i in tokenize(@rid, '\s+') satisfies exists(//ref[@id = $i]))">bibr xrefs must target references.</assert>
      <assert role="error" test="not(@ref-type = 'sec') or (every $i in tokenize(@rid, '\s+') satisfies exists((//sec | //book-part)[@id = $i]))">sec xrefs must target sections or book parts.</assert>
    </rule>
  </pattern>

  <pattern id="resource-paths">
    <rule context="graphic | media | supplementary-material[@xlink:href]">
      <assert id="p2h-resource-href" role="error" test="normalize-space(@xlink:href) != ''">Resource-bearing elements must have xlink:href.</assert>
      <assert id="p2h-resource-relative" role="error"
              test="starts-with(@xlink:href, '../assets/content/') and not(contains(substring-after(@xlink:href, '../'), '../'))">
        Content resources must resolve below assets/content from content/document.xml.
      </assert>
    </rule>
  </pattern>
</schema>
