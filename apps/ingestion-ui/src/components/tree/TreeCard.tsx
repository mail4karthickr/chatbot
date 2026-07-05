import { useMemo } from 'react'
import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { openModal } from '../../features/ui/uiSlice'
import { buildTree } from '../../utils/tree'
import { EmptyBucketIcon } from '../icons'
import { FolderRow } from './FolderRow'

export function TreeCard() {
  const dispatch = useAppDispatch()
  const { bucket, files, folders, status } = useAppSelector((s) => s.s3)

  const tree = useMemo(
    () => (files && bucket ? buildTree(files, folders, bucket) : null),
    [files, folders, bucket],
  )

  const loading = status === 'loading' && files === null

  return (
    <section className="tree-card">
      <div className="tree-header">
        <div className="col name-col">Name</div>
        <div className="col size-col">Size</div>
        <div className="col date-col">Last modified</div>
      </div>
      <div className="tree-body">
        {loading && <div className="empty">Loading…</div>}
        {tree && <FolderRow key={tree.path} node={tree} />}
        {tree && tree.children.length === 0 && (
          <div className="empty-state">
            <div className="empty-icon" aria-hidden="true">
              <EmptyBucketIcon />
            </div>
            <h3>This bucket is empty</h3>
            <p>Upload files or create a folder to get started.</p>
            <div className="empty-actions">
              <button
                className="btn primary"
                onClick={() => dispatch(openModal({ kind: 'upload', targetPath: '' }))}
              >
                Upload files
              </button>
              <button
                className="btn secondary"
                onClick={() => dispatch(openModal({ kind: 'newFolder', parentPath: '' }))}
              >
                New folder
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
