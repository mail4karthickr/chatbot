import { useAppDispatch } from '../../app/hooks'
import { openModal } from '../../features/ui/uiSlice'
import type { FileNode } from '../../utils/tree'
import { formatBytes, formatDate, isImageName } from '../../utils/format'
import { DocScanIcon, EyeIcon, FileIcon, TrashIcon } from '../icons'

const PARSEABLE_EXTS = new Set(['pdf', 'docx', 'pptx', 'html', 'htm', 'md'])

function isParseable(name: string): boolean {
  const i = name.lastIndexOf('.')
  if (i < 0) return false
  return PARSEABLE_EXTS.has(name.slice(i + 1).toLowerCase())
}

export function FileRow({ node }: { node: FileNode }) {
  const dispatch = useAppDispatch()
  const stop = (e: React.MouseEvent) => e.stopPropagation()
  const isImage = isImageName(node.name)
  const canParse = isParseable(node.name)
  const openImagePreview = () =>
    dispatch(
      openModal({ kind: 'imagePreview', key: node.path, name: node.name }),
    )
  const openParsePreview = () =>
    dispatch(
      openModal({ kind: 'parsePreview', key: node.path, name: node.name }),
    )
  return (
    <div className="row file-row">
      <div className="row-name" style={{ paddingLeft: node.depth * 24 }}>
        <span className="chev-spacer" />
        <FileIcon />
        {isImage ? (
          <button
            type="button"
            className="name mono name-link"
            onClick={openImagePreview}
            title="Preview image"
          >
            {node.name}
          </button>
        ) : canParse ? (
          <button
            type="button"
            className="name mono name-link"
            onClick={openParsePreview}
            title="Preview Docling parse"
          >
            {node.name}
          </button>
        ) : (
          <span className="name mono">{node.name}</span>
        )}
      </div>
      <div className="row-size">{formatBytes(node.file.size)}</div>
      <div className="row-date">{formatDate(node.file.last_modified)}</div>
      <div className="row-actions" onClick={stop}>
        {isImage && (
          <button
            className="icon-btn"
            title="Preview image"
            onClick={openImagePreview}
          >
            <EyeIcon />
          </button>
        )}
        {canParse && (
          <button
            className="icon-btn"
            title="Preview Docling parse"
            onClick={openParsePreview}
          >
            <DocScanIcon />
          </button>
        )}
        <button
          className="icon-btn danger"
          title="Delete this file"
          onClick={() =>
            dispatch(
              openModal({
                kind: 'deleteFile',
                key: node.path,
                name: node.name,
                size: node.file.size,
              }),
            )
          }
        >
          <TrashIcon />
        </button>
      </div>
    </div>
  )
}
