import { useMemo } from 'react'
import { useAppSelector } from '../app/hooks'
import { buildTree, collectVersions } from '../utils/tree'
import { formatBytes } from '../utils/format'

export function Stats() {
  const { bucket, files, folders } = useAppSelector((s) => s.s3)

  const tree = useMemo(
    () => (files && bucket ? buildTree(files, folders, bucket) : null),
    [files, folders, bucket],
  )
  const versions = useMemo(
    () => (tree ? collectVersions(tree) : new Set<string>()),
    [tree],
  )

  if (!files) return null
  const count = files.length
  const totalSize = tree?.totalSize ?? 0

  return (
    <div className="stats">
      <span>
        <b>{count}</b> object{count === 1 ? '' : 's'}
      </span>
      <span className="dot" />
      <span>
        <b>{formatBytes(totalSize)}</b> total
      </span>
      <span className="dot" />
      <span>
        <b>{versions.size}</b> version{versions.size === 1 ? '' : 's'}
      </span>
    </div>
  )
}
