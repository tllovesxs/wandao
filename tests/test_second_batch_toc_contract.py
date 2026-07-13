import argparse
import unittest
import xml.etree.ElementTree as ET

from plugins.ima.backend.ima_knowledge import KnowledgeEntry, selected_entries
from plugins.onenote.backend.export_onenote import TocNode, selected_pages
from plugins.youdao.backend.export_youdao import RemoteNode, select_export_documents
from plugins.yinxiang.backend.export_yinxiang import select_enex_notes
from plugins.zsxq.backend.export_zsxq import select_toc_items


class SecondBatchTocContractTests(unittest.TestCase):
    def test_ima_filters_the_export_id_emitted_by_the_nodes_toc(self) -> None:
        folder = KnowledgeEntry('kb', 'KB', 'folder', 'Folder', '', [], True)
        doc = KnowledgeEntry('kb', 'KB', 'doc', 'Document', 'folder', [], False)

        self.assertEqual([entry.export_id for entry in selected_entries([folder, doc], [doc.export_id])], [doc.export_id])

    def test_onenote_filters_the_page_export_id_emitted_by_the_nodes_toc(self) -> None:
        folder = TocNode('', 'notebook', 'notebook', 'Notebook', '', False, 0, [])
        page = TocNode('page-id', 'page', 'page', 'Page', 'notebook', True, 1, [])
        args = argparse.Namespace(selected_doc_ids=['page-id'])

        self.assertEqual([node.id for node in selected_pages(args, [folder, page])], ['page-id'])

    def test_youdao_filters_only_selected_non_folder_export_ids(self) -> None:
        folder = RemoteNode('folder-id', 'Folder', True)
        doc = RemoteNode('doc-id', 'Note.note', False, parent_id='folder-id', raw={'createTime': '123'})

        self.assertEqual([node.id for node in select_export_documents([folder, doc], ['doc-id'])], ['doc-id'])
        self.assertEqual(doc.created_time, 123.0)

    def test_yinxiang_filters_enex_notes_by_the_guid_emitted_by_the_toc(self) -> None:
        first = ET.fromstring('<note><guid>first-guid</guid></note>')
        selected = ET.fromstring('<note><guid>selected-guid</guid></note>')

        self.assertEqual(select_enex_notes([first, selected], {'selected-guid'}), [selected])

    def test_zsxq_column_filters_the_toc_key_emitted_by_the_group_adapter(self) -> None:
        toc = {'groups': [{'groupTitle': 'Section', 'topics': [{'key': 'toc:3:0', 'title': 'Article'}, {'key': 'toc:3:1', 'title': 'Other'}]}]}
        args = argparse.Namespace(selected_toc_keys=['toc:3:0'], toc_group_pattern=None, toc_title_pattern=None, link_pattern=None, limit=0)

        self.assertEqual([item['key'] for item in select_toc_items(toc, args)], ['toc:3:0'])


if __name__ == '__main__':
    unittest.main()
