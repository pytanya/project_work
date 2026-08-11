import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FileUpload from '../FileUpload'

describe('FileUpload', () => {
  it('вызывает onUpload при выборе файла', async () => {
    const onUpload = vi.fn()
    const { container } = render(<FileUpload onUpload={onUpload} />)
    const input = container.querySelector('input[type="file"]')
    const file = new File(['data'], 'book.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file))
  })

  it('вызывает onUpload при drop', async () => {
    const onUpload = vi.fn()
    render(<FileUpload onUpload={onUpload} />)
    const dropzone = screen.getByText(/Перетащите PDF/).closest('div')
    const file = new File(['data'], 'doc.txt', { type: 'text/plain' })
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } })
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file))
  })
})
