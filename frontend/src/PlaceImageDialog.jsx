import { useEffect, useId, useRef, useState } from 'react'
import { requestPlaceImages } from './services/placeImagesApi.js'

function PlaceImageDialog({ place, onClose }) {
  const dialogRef = useRef(null)
  const titleId = useId()
  const canLoad = place.verified === true
    && typeof place.place_id === 'string'
    && place.place_id.trim().length > 0
  const [attempt, setAttempt] = useState(0)
  const [failedImages, setFailedImages] = useState(() => new Set())
  const [state, setState] = useState(() => ({
    status: canLoad ? 'loading' : 'empty',
    name: place.title,
    images: [],
    error: '',
  }))

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return

    const previous = document.activeElement
    if (!dialog.open) dialog.showModal()

    return () => {
      if (dialog.open) dialog.close()

      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true })
      } else {
        document.getElementById('travel-plan-title')?.focus({ preventScroll: true })
      }
    }
  }, [])

  useEffect(() => {
    if (!canLoad) return
    const controller = new AbortController()

    requestPlaceImages(place.place_id, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        setState({
          status: 'ready',
          name: result.name,
          images: result.images,
          error: '',
        })
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setState({
          status: 'error',
          name: place.title,
          images: [],
          error: typeof error?.message === 'string'
            ? error.message : '사진을 불러오지 못했습니다.',
        })
      })

    return () => controller.abort()
  }, [canLoad, place.place_id, place.title, attempt])

  function retry() {
    setState({ status: 'loading', name: place.title, images: [], error: '' })
    setFailedImages(new Set())
    setAttempt((previous) => previous + 1)
  }

  const isEmpty = state.status === 'empty'
    || (state.status === 'ready' && state.images.length === 0)

  return (
    <dialog
      ref={dialogRef}
      className="place-image-dialog"
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <header className="place-image-header">
        <h2 id={titleId}>{state.name} 사진</h2>
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          autoFocus
        >
          닫기
        </button>
      </header>

      <div className="place-image-body" aria-busy={state.status === 'loading'}>
        {state.status === 'loading' && (
          <p className="place-image-message" role="status">
            사진을 불러오는 중입니다.
          </p>
        )}

        {isEmpty && (
          <p className="place-image-message" role="status">
            현재 표시할 수 있는 사진이 없습니다.
          </p>
        )}

        {state.status === 'error' && (
          <div className="place-image-message is-error" role="alert">
            <p>{state.error}</p>
            <button type="button" className="secondary-button" onClick={retry}>
              다시 불러오기
            </button>
          </div>
        )}

        {state.status === 'ready' && state.images.length > 0 && (
          <div className="place-image-gallery">
            {state.images.map((image) => (
              <figure key={image.image_id}>
                {failedImages.has(image.image_id) ? (
                  <p className="place-image-message">
                    이 사진을 불러오지 못했습니다.
                  </p>
                ) : (
                  <img
                    src={image.src}
                    alt={image.alt}
                    loading="lazy"
                    decoding="async"
                    onError={() => setFailedImages((previous) => (
                      new Set([...previous, image.image_id])
                    ))}
                  />
                )}
                <figcaption>{image.caption}</figcaption>
              </figure>
            ))}
          </div>
        )}
      </div>
    </dialog>
  )
}

export default PlaceImageDialog