import { useState } from 'react'
import { useAppDispatch } from '../../app/hooks'
import { openModal } from '../../features/ui/uiSlice'
import type { FolderNode } from '../../utils/tree'
import { formatBytes } from '../../utils/format'
import { BucketIcon, ChevronIcon, FolderIcon, NewFolderIcon, TrashIcon, UploadIcon } from '../icons'
import { FileRow } from './FileRow'

export function FolderRow({ node }: { node: FolderNode }) {
  const [open, setOpen] = useState(true)
  const dispatch = useAppDispatch()
  const isBucket = node.depth === 0
  const isVersion = node.depth === 2
  const stop = (e: React.MouseEvent) => e.stopPropagation()
  const targetPath = isBucket ? '' : node.path

  return (
    <>
      <div
        className={`row folder-row${isBucket ? ' bucket-row' : ''}${isVersion ? ' is-version' : ''}`}
        onClick={() => setOpen((o) => !o)}
        role="button"
        aria-expanded={open}
      >
        <div className="row-name" style={{ paddingLeft: node.depth * 24 }}>
          <ChevronIcon open={open} />
          {isBucket ? <BucketIcon /> : <FolderIcon />}
          {isBucket && <span className="s3-prefix">s3://</span>}
          <span className={isBucket ? 'name bucket-name' : 'name'}>{node.name}</span>
          {isBucket && <span className="badge bucket-badge">bucket</span>}
          {isVersion && <span className="badge">version</span>}
        </div>
        <div className="row-size">
          {node.fileCount} · {formatBytes(node.totalSize)}
        </div>
        <div className="row-date">—</div>
        <div className="row-actions" onClick={stop}>
          <button
            className="icon-btn"
            title="Upload files here"
            onClick={() => dispatch(openModal({ kind: 'upload', targetPath }))}
          >
            <UploadIcon />
          </button>
          <button
            className="icon-btn"
            title="Create folder inside"
            onClick={() => dispatch(openModal({ kind: 'newFolder', parentPath: targetPath }))}
          >
            <NewFolderIcon />
          </button>
          {!isBucket && (
            <button
              className="icon-btn danger"
              title="Delete this folder"
              onClick={() =>
                dispatch(
                  openModal({
                    kind: 'delete',
                    path: node.path,
                    fileCount: node.fileCount,
                    totalSize: node.totalSize,
                  }),
                )
              }
            >
              <TrashIcon />
            </button>
          )}
        </div>
      </div>
      {open && node.children.map((child) =>
        child.kind === 'folder' ? (
          <FolderRow key={child.path} node={child} />
        ) : (
          <FileRow key={child.path} node={child} />
        ),
      )}
    </>
  )
}
