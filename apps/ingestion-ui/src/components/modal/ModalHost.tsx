import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { closeModal } from '../../features/ui/uiSlice'
import type { ModalState } from '../../features/ui/uiSlice'
import { DeleteConfirm } from './DeleteConfirm'
import { DeleteFileConfirm } from './DeleteFileConfirm'
import { ImagePreview } from './ImagePreview'
import { Modal } from './Modal'
import { NewFolderForm } from './NewFolderForm'
import { ParsePreview } from './ParsePreview'
import { ResetConfirm } from './ResetConfirm'
import { UploadForm } from './UploadForm'

function titleFor(modal: NonNullable<ModalState>): string {
  switch (modal.kind) {
    case 'upload': return 'Upload files'
    case 'newFolder': return 'New folder'
    case 'delete': return 'Delete folder'
    case 'deleteFile': return 'Delete file'
    case 'imagePreview': return modal.name
    case 'parsePreview': return `Docling parse — ${modal.name}`
    case 'reset': return 'Reset everything'
  }
}

export function ModalHost() {
  const dispatch = useAppDispatch()
  const modal = useAppSelector((s) => s.ui.modal)
  const bucket = useAppSelector((s) => s.s3.bucket)

  if (!modal || !bucket) return null

  return (
    <Modal
      title={titleFor(modal)}
      onClose={() => dispatch(closeModal())}
      size={
        modal.kind === 'imagePreview' || modal.kind === 'parsePreview'
          ? 'lg'
          : 'sm'
      }
    >
      {modal.kind === 'upload' && (
        <UploadForm bucket={bucket} targetPath={modal.targetPath} />
      )}
      {modal.kind === 'newFolder' && (
        <NewFolderForm bucket={bucket} parentPath={modal.parentPath} />
      )}
      {modal.kind === 'delete' && (
        <DeleteConfirm
          bucket={bucket}
          path={modal.path}
          fileCount={modal.fileCount}
          totalSize={modal.totalSize}
        />
      )}
      {modal.kind === 'deleteFile' && (
        <DeleteFileConfirm
          bucket={bucket}
          fileKey={modal.key}
          name={modal.name}
          size={modal.size}
        />
      )}
      {modal.kind === 'imagePreview' && (
        <ImagePreview
          bucket={bucket}
          fileKey={modal.key}
          name={modal.name}
        />
      )}
      {modal.kind === 'parsePreview' && (
        <ParsePreview bucket={bucket} fileKey={modal.key} />
      )}
      {modal.kind === 'reset' && <ResetConfirm />}
    </Modal>
  )
}
