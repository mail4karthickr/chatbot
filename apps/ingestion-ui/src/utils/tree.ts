import type { S3File } from '../api'

export type FolderNode = {
  kind: 'folder'
  name: string
  path: string
  depth: number
  children: TreeNode[]
  fileCount: number
  totalSize: number
}

export type FileNode = {
  kind: 'file'
  name: string
  path: string
  depth: number
  file: S3File
}

export type TreeNode = FolderNode | FileNode

function ensureFolder(root: FolderNode, path: string): FolderNode {
  const parts = path.split('/').filter(Boolean)
  let cur: FolderNode = root
  for (let i = 0; i < parts.length; i++) {
    const segment = parts[i]
    const p = parts.slice(0, i + 1).join('/')
    let child = cur.children.find(
      (c): c is FolderNode => c.kind === 'folder' && c.name === segment,
    )
    if (!child) {
      child = {
        kind: 'folder', name: segment, path: p, depth: i + 1,
        children: [], fileCount: 0, totalSize: 0,
      }
      cur.children.push(child)
    }
    cur = child
  }
  return cur
}

export function buildTree(files: S3File[], folders: string[], bucketName: string): FolderNode {
  // The tree is rooted at the bucket. Path layout inside the bucket is
  // {doc_id}/{doc_version}/{...files}, so:
  //   depth 0 = bucket, 1 = doc_id, 2 = doc_version, 3 = file
  // `folders` are empty-folder markers — we seed them so they still appear when they hold no files.
  const bucket: FolderNode = {
    kind: 'folder', name: bucketName, path: bucketName, depth: 0,
    children: [], fileCount: 0, totalSize: 0,
  }
  for (const folderPath of folders) {
    ensureFolder(bucket, folderPath)
  }
  for (const file of files) {
    const parts = file.key.split('/')
    const dirPath = parts.slice(0, -1).join('/')
    const parent = dirPath ? ensureFolder(bucket, dirPath) : bucket
    const fname = parts[parts.length - 1]
    parent.children.push({
      kind: 'file', name: fname, path: file.key, depth: parts.length, file,
    })
  }
  function rollup(node: FolderNode): { count: number; size: number } {
    let count = 0, size = 0
    for (const c of node.children) {
      if (c.kind === 'file') {
        count++
        size += c.file.size
      } else {
        const r = rollup(c)
        count += r.count
        size += r.size
      }
    }
    node.fileCount = count
    node.totalSize = size
    return { count, size }
  }
  rollup(bucket)
  function sort(node: FolderNode) {
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    for (const c of node.children) if (c.kind === 'folder') sort(c)
  }
  sort(bucket)
  return bucket
}

export function collectVersions(bucket: FolderNode): Set<string> {
  // Depth-2 folder = doc_version (bucket → doc_id → version).
  const set = new Set<string>()
  for (const docIdNode of bucket.children) {
    if (docIdNode.kind !== 'folder') continue
    for (const child of docIdNode.children) {
      if (child.kind === 'folder') set.add(child.name)
    }
  }
  return set
}
