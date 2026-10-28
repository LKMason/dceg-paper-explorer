(function (root) {
  'use strict';
  function normalizePMID(value) {
    const text = String(value).trim().replace(/^PMID\s*:\s*/i, '');
    const match = text.match(/^(?:https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/)?(\d{1,10})\/?$/i);
    if (!match || !Number(match[1])) throw new Error('Enter a valid numeric PubMed ID (PMID).');
    return String(Number(match[1]));
  }

  function parse(xml, pmid, Parser = root.DOMParser) {
    const doc = new Parser().parseFromString(xml, 'application/xml');
    if (doc.querySelector('parsererror')) throw new Error('PubMed returned an unreadable record. Please try again.');
    const record = [...doc.querySelectorAll('PubmedArticle, PubmedBookArticle')].find(el =>
      el.querySelector('MedlineCitation > PMID, BookDocument > PMID')?.textContent.trim() === pmid);
    if (!record) throw new Error(`No PubMed paper was found for PMID ${pmid}.`);
    const book = record.tagName === 'PubmedBookArticle';
    const article = record.querySelector(book ? 'BookDocument' : 'MedlineCitation > Article');
    // Match the base collector's whitespace and inline-markup handling.
    const plain = value => String(value || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const text = (el, selector) => plain(el?.querySelector(selector)?.textContent);
    const title = text(article, 'ArticleTitle') || text(article, 'Book > BookTitle');
    if (!title) throw new Error('This PubMed record has no title and cannot be embedded.');
    const authorTypes = {}, authors = [];
    for (const author of article.querySelectorAll(':scope > AuthorList > Author')) {
      const group = author.querySelector('CollectiveName');
      const name = group ? plain(group.textContent) : [text(author, 'ForeName') || text(author, 'Initials'), text(author, 'LastName')].filter(Boolean).join(' ');
      if (name) {authors.push(name); authorTypes[name] = group ? 'group' : 'person';}
    }
    const abstract = [...article.querySelectorAll(':scope > Abstract > AbstractText')].map(el => {
      const body = plain(el.textContent);
      return body ? (el.getAttribute('Label') ? el.getAttribute('Label') + ': ' : '') + body : '';
    }).filter(Boolean).join('\n\n');
    const dateNode = article.querySelector(book ? 'ContributionDate, Book > PubDate' : 'Journal > JournalIssue > PubDate, ArticleDate');
    const year = text(dateNode, 'Year') || text(dateNode, 'MedlineDate').match(/\d{4}/)?.[0] || '';
    const month = text(dateNode, 'Month'), day = text(dateNode, 'Day');
    const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
    const monthNumber = /^\d{1,2}$/.test(month) ? Number(month) : months.indexOf(month.toLowerCase().slice(0, 3)) + 1;
    const date = year + (monthNumber >= 1 && monthNumber <= 12 ? '-' + String(monthNumber).padStart(2, '0') + (/^\d{1,2}$/.test(day) ? '-' + day.padStart(2, '0') : '') : '');
    const doi = text(record, 'ArticleId[IdType="doi"]') || text(article, 'ELocationID[EIdType="doi"]');
    const tags = [...record.querySelectorAll('MeshHeading > DescriptorName, KeywordList > Keyword')].map(el => plain(el.textContent));
    return {paper: {id: 'pmid:' + pmid, pmid, title, abstract, authors, firstAuthor: authors[0] || 'Unknown author',
      year, date, journal: text(article, 'Journal > Title') || text(article, 'Book > BookTitle'),
      doi: doi.toLowerCase(), url: 'https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/', branches: [],
      types: [...article.querySelectorAll('PublicationType')].map(el => plain(el.textContent)).join('; ') || 'PubMed record',
      tags: [...new Set(tags)].join('; '), added: true, addedAt: new Date().toISOString()}, authorTypes};
  }

  async function lookup(value, {fetch: fetcher = root.fetch, signal, Parser = root.DOMParser} = {}) {
    const pmid = normalizePMID(value);
    const url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&retmode=xml&id=' + pmid;
    const response = await fetcher(url, {signal, credentials: 'omit'});
    if (!response.ok) throw new Error(response.status === 429 ? 'PubMed is receiving too many requests. Wait a moment and try again.' : `PubMed lookup failed (${response.status}). Please try again.`);
    return parse(await response.text(), pmid, Parser);
  }
  const api = {normalizePMID, parse, lookup};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PaperPubMed = api;
})(globalThis);
