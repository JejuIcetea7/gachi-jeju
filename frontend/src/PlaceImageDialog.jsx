import { useEffect, useId, useRef, useState } from 'react'
import { requestPlaceDetails } from './services/placeDetailsApi.js'
import { requestPlaceImages } from './services/placeImagesApi.js'
import { groupAccessibilityFacts } from './services/placeDetails.js'

function displayText(value, fallback) {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function PlaceDetailsContent({ state, onRetry }) {
  if (state.status === 'loading') {
    return <p className="place-image-message" role="status">장소 상세 정보를 불러오는 중입니다.</p>
  }

  if (state.status === 'unverified') {
    return (
      <p className="place-image-message" role="status">
        여행지 자료 연결이 확인되지 않아 상세 정보를 조회할 수 없습니다.
      </p>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="place-image-message is-error" role="alert">
        <p>{state.error}</p>
        <button type="button" className="secondary-button" onClick={onRetry}>
          상세 정보 다시 불러오기
        </button>
      </div>
    )
  }

  const detail = state.data
  const groups = groupAccessibilityFacts(detail.accessibility_facts)

  return (
    <div className="place-details-content">
      <p className="place-detail-source">
        등록된 조사 원문과 지점별 설명입니다.
        시설·지점별 내용을 확인해 주세요. 추천 문구와 기호는 원문대로 표시합니다.
      </p>

      <section className="place-detail-section">
        <h3>기본 정보</h3>
        <dl className="place-detail-fields">
          <div>
            <dt>장소 종류</dt>
            <dd>{displayText(detail.category, '분류 정보 미등록')}</dd>
          </div>
          <div>
            <dt>휴무 정보</dt>
            <dd>{displayText(detail.closed_days, '휴무 정보 미등록')}</dd>
          </div>
        </dl>
      </section>

      <section className="place-detail-section">
        <h3>시설·이동약자 추천 정보</h3>
        <p className="place-detail-note">
          ‘미설치’는 조사 원문의 표기이며, 자료 미등록과 구분합니다.
        </p>
        {groups.length === 0 ? (
          <p className="place-image-message">등록된 시설·추천 원문이 없습니다.</p>
        ) : (
          <div className="place-detail-groups">
            {groups.map((group) => (
              <section
                className="place-detail-fact-group"
                key={JSON.stringify([group.section, group.facility_name])}
              >
                <h4>{displayText(group.section, '분류 미등록')}</h4>
                <p className="place-detail-facility">
                  {displayText(group.facility_name, '시설명 미등록')}
                </p>
                <dl className="place-detail-fields">
                  {group.facts.map((fact, index) => (
                    <div key={index}>
                      <dt>{displayText(fact.description, '항목명 미등록')}</dt>
                      <dd>{displayText(fact.raw_value, '값 미등록')}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        )}
      </section>

      <section className="place-detail-section">
        <h3>관람 동선·지점별 접근성</h3>
        {detail.spots.length === 0 ? (
          <p className="place-image-message">등록된 지점별 설명이 없습니다.</p>
        ) : (
          <div className="place-detail-spots">
            {detail.spots.map((spot, index) => (
              <details key={index} className="place-detail-spot">
                <summary>{displayText(spot.name, '지점명 미등록')}</summary>
                <p>{spot.description}</p>
              </details>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function PlacePhotosContent({ state, failedImages, onRetry, onImageError }) {
  const isEmpty = state.status === 'empty'
    || (state.status === 'ready' && state.images.length === 0)

  return (
    <>
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
          <button type="button" className="secondary-button" onClick={onRetry}>
            사진 다시 불러오기
          </button>
        </div>
      )}

      {state.status === 'ready' && state.images.length > 0 && (
        <div className="place-image-gallery">
          {state.images.map((image) => (
            <figure key={image.image_id}>
              {failedImages.has(image.image_id) ? (
                <p className="place-image-message">이 사진을 불러오지 못했습니다.</p>
              ) : (
                <img
                  src={image.src}
                  alt={image.alt}
                  loading="lazy"
                  decoding="async"
                  onError={() => onImageError(image.image_id)}
                />
              )}
              <figcaption>{image.caption}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </>
  )
}

function PlaceImageDialog({ place, onClose }) {
  const dialogRef = useRef(null)
  const titleId = useId()
  const canLoad = place.verified === true
    && typeof place.place_id === 'string'
    && place.place_id.trim().length > 0
  const planNotes = Array.isArray(place.accessibility_notes)
    ? place.accessibility_notes.filter((value) => (
      typeof value === 'string' && value.trim().length > 0
    ))
    : []
  const [activeView, setActiveView] = useState('details')
  const [detailAttempt, setDetailAttempt] = useState(0)
  const [photoAttempt, setPhotoAttempt] = useState(0)
  const [failedImages, setFailedImages] = useState(() => new Set())
  const [details, setDetails] = useState(() => ({
    status: canLoad ? 'loading' : 'unverified',
    data: null,
    error: '',
  }))
  const [photos, setPhotos] = useState(() => ({
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

    requestPlaceDetails(place.place_id, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        setDetails({ status: 'ready', data: result, error: '' })
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setDetails({
          status: 'error',
          data: null,
          error: typeof error?.message === 'string'
            ? error.message : '장소 상세 정보를 불러오지 못했습니다.',
        })
      })

    return () => controller.abort()
  }, [canLoad, place.place_id, detailAttempt])

  useEffect(() => {
    if (!canLoad) return
    const controller = new AbortController()

    requestPlaceImages(place.place_id, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        setPhotos({
          status: 'ready',
          name: result.name,
          images: result.images,
          error: '',
        })
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setPhotos({
          status: 'error',
          name: place.title,
          images: [],
          error: typeof error?.message === 'string'
            ? error.message : '사진을 불러오지 못했습니다.',
        })
      })

    return () => controller.abort()
  }, [canLoad, place.place_id, place.title, photoAttempt])

  function retryDetails() {
    setDetails({ status: 'loading', data: null, error: '' })
    setDetailAttempt((previous) => previous + 1)
  }

  function retryPhotos() {
    setPhotos({ status: 'loading', name: place.title, images: [], error: '' })
    setFailedImages(new Set())
    setPhotoAttempt((previous) => previous + 1)
  }

  const name = details.data?.name ?? photos.name
  const isLoading = activeView === 'details'
    ? details.status === 'loading'
    : photos.status === 'loading'

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
        <h2 id={titleId}>{name} 상세 정보</h2>
        <button type="button" className="secondary-button" onClick={onClose} autoFocus>
          닫기
        </button>
      </header>

      <div className="place-detail-view-buttons" role="group" aria-label="장소 정보 보기">
        <button
          type="button"
          aria-pressed={activeView === 'details'}
          onClick={() => setActiveView('details')}
        >
          시설·동선 정보
        </button>
        <button
          type="button"
          aria-pressed={activeView === 'photos'}
          onClick={() => setActiveView('photos')}
        >
          사진
        </button>
      </div>

      <div key={activeView} className="place-image-body" aria-busy={isLoading}>
        {activeView === 'details' ? (
          <div className="place-details-content">
            {planNotes.length > 0 && (
              <section className="place-plan-accessibility">
                <h3>이번 일정의 접근성 안내</h3>
                <p className="place-detail-note">
                  일정을 만들 때 작성된 안내입니다. 아래 조사 원문과 구분해 확인해 주세요.
                </p>
                <ul className="plan-notes">
                  {planNotes.map((text, index) => <li key={index}>{text}</li>)}
                </ul>
              </section>
            )}
            <PlaceDetailsContent state={details} onRetry={retryDetails} />
          </div>
        ) : (
          <PlacePhotosContent
            state={photos}
            failedImages={failedImages}
            onRetry={retryPhotos}
            onImageError={(imageId) => setFailedImages((previous) => (
              new Set([...previous, imageId])
            ))}
          />
        )}
      </div>
    </dialog>
  )
}

export default PlaceImageDialog
