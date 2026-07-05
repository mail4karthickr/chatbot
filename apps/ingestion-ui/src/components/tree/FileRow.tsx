import { useAppDispatch } from '../../app/hooks'
import { openModal } from '../../features/ui/uiSlice'
import type { FileNode } from '../../utils/tree'
import { formatBytes, formatDate } from '../../utils/format'
import { FileIcon, TrashIcon } from '../icons'

export function FileRow({ node }: { node: FileNode }) {
  const dispatch = useAppDispatch()
  const stop = (e: React.MouseEvent) => e.stopPropagation()
  return (
    <div className="row file-row">
      <div className="row-name" style={{ paddingLeft: node.depth * 24 }}>
        <span className="chev-spacer" />
        <FileIcon />
        <span className="name mono">{node.name}</span>
      </div>
      <div className="row-size">{formatBytes(node.file.size)}</div>
      <div className="row-date">{formatDate(node.file.last_modified)}</div>
      <div className="row-actions" onClick={stop}>
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
