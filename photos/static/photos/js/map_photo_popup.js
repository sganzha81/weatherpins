(function () {
    'use strict';

    window.createMapPhotoPopup = function ({map, popupElement, getFeature, closePopup}) {
        const viewport = map.getViewport();
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        svg.classList.add('popup-connector');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        svg.style.display = 'none';
        svg.append(path);
        // Above map layers, below both OpenLayers overlay containers and the card.
        viewport.insertBefore(svg, viewport.querySelector('.ol-overlaycontainer'));

        const mobile = window.matchMedia('(max-width: 768px), (hover: none), (pointer: coarse)');
        const events = new AbortController();
        let offsetX = 0;
        let offsetY = 0;
        let drag = null;
        let frame = null;
        let destroyed = false;

        function applyOffset() {
            popupElement.style.setProperty('--drag-x', `${offsetX}px`);
            popupElement.style.setProperty('--drag-y', `${offsetY}px`);
        }

        function updateConnector() {
            svg.style.display = 'none';
            if (mobile.matches || popupElement.style.display === 'none' ||
                Math.hypot(offsetX, offsetY) < 2) return;
            const feature = getFeature();
            const coordinate = feature?.getGeometry()?.getCoordinates();
            if (!coordinate || !coordinate.every(Number.isFinite)) return;
            const pixel = map.getPixelFromCoordinate(coordinate);
            const size = map.getSize();
            const card = popupElement.querySelector('.popup-card');
            if (!card || !size || !pixel || !pixel.every(Number.isFinite) ||
                pixel[0] < 0 || pixel[1] < 0 || pixel[0] > size[0] || pixel[1] > size[1]) return;
            const mapRect = viewport.getBoundingClientRect();
            if (!mapRect.width || !mapRect.height || !size[0] || !size[1]) return;
            const rect = card.getBoundingClientRect();
            // Both the card bounds and map pixel are expressed in viewport-local
            // CSS pixels, including any scale on the map container.
            const x = pixel[0] * mapRect.width / size[0];
            const y = pixel[1] * mapRect.height / size[1];
            const left = rect.left - mapRect.left;
            const right = rect.right - mapRect.left;
            const top = rect.top - mapRect.top;
            const bottom = rect.bottom - mapRect.top;
            if (x >= left && x <= right && y >= top && y <= bottom) return;
            const startX = Math.max(left, Math.min(right, x));
            const startY = Math.max(top, Math.min(bottom, y));
            const dx = x - startX;
            const dy = y - startY;
            const length = Math.hypot(dx, dy);
            if (length < 4) return;
            const bend = Math.min(24, length * 0.14);
            let bendX = -dy / length * bend;
            let bendY = dx / length * bend;
            const midX = (startX + x) / 2;
            const midY = (startY + y) / 2;
            // Keep the curve's control point outside the card's attachment edge.
            if ((startX === left && midX + bendX > left) ||
                (startX === right && midX + bendX < right) ||
                (startY === top && midY + bendY > top) ||
                (startY === bottom && midY + bendY < bottom)) {
                bendX = -bendX;
                bendY = -bendY;
            }
            svg.setAttribute('viewBox', `0 0 ${mapRect.width} ${mapRect.height}`);
            path.setAttribute('d', `M ${startX} ${startY} Q ${midX + bendX} ${midY + bendY} ${x} ${y}`);
            svg.style.display = 'block';
        }

        function schedule() {
            if (destroyed || frame !== null) return;
            frame = requestAnimationFrame(() => {
                frame = null;
                updateConnector();
            });
        }

        function finishDrag(event) {
            if (!drag || (event && event.pointerId !== drag.id)) return;
            const previous = drag;
            drag = null;
            popupElement.querySelector('.popup-card')?.classList.remove('is-dragging');
            if (previous.header.hasPointerCapture(previous.id)) {
                previous.header.releasePointerCapture(previous.id);
            }
            schedule();
        }

        function reset() {
            finishDrag();
            offsetX = 0;
            offsetY = 0;
            applyOffset();
            svg.style.display = 'none';
            path.removeAttribute('d');
        }

        function constrainOffset() {
            const card = popupElement.querySelector('.popup-card');
            if (!card) return;
            const rect = card.getBoundingClientRect();
            const mapRect = viewport.getBoundingClientRect();
            const left = Math.max(0, mapRect.left) + 8;
            const right = Math.min(window.innerWidth, mapRect.right) - 8;
            const top = Math.max(0, mapRect.top) + 8;
            const bottom = Math.min(window.innerHeight, mapRect.bottom) - 8;
            if (rect.width <= right - left) {
                offsetX += Math.max(left - rect.left, Math.min(right - rect.right, 0));
            }
            if (rect.height <= bottom - top) {
                offsetY += Math.max(top - rect.top, Math.min(bottom - rect.bottom, 0));
            }
            applyOffset();
        }

        popupElement.addEventListener('pointerdown', event => {
            const header = event.target.closest('.popup-header');
            if (!header || event.button !== 0 || event.isPrimary === false ||
                event.target.closest('button, a, input, select, textarea, [contenteditable], [role="button"]')) return;
            event.preventDefault();
            event.stopPropagation();
            drag = {id: event.pointerId, header, x: event.clientX, y: event.clientY, offsetX, offsetY};
            popupElement.querySelector('.popup-card')?.classList.add('is-dragging');
            header.setPointerCapture(event.pointerId);
        }, {signal: events.signal});

        popupElement.addEventListener('click', event => {
            if (event.target.closest('.popup-close-btn') && popupElement.style.display === 'none') {
                map.getTargetElement()?.focus({preventScroll: true});
            }
        }, {signal: events.signal});

        window.addEventListener('pointermove', event => {
            if (!drag || event.pointerId !== drag.id) return;
            event.preventDefault();
            event.stopPropagation();
            offsetX = drag.offsetX + event.clientX - drag.x;
            offsetY = drag.offsetY + event.clientY - drag.y;
            applyOffset();
            constrainOffset();
            schedule();
        }, {passive: false, signal: events.signal});
        window.addEventListener('pointerup', finishDrag, {signal: events.signal});
        window.addEventListener('pointercancel', finishDrag, {signal: events.signal});
        popupElement.addEventListener('lostpointercapture', finishDrag, {signal: events.signal});
        window.addEventListener('blur', () => finishDrag(), {signal: events.signal});
        window.addEventListener('resize', () => {
            if (popupElement.style.display !== 'none' && Math.hypot(offsetX, offsetY) >= 2) {
                constrainOffset();
            }
            schedule();
        }, {signal: events.signal});
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !event.defaultPrevented &&
                popupElement.style.display !== 'none' &&
                !document.body.classList.contains('image-lightbox-open')) {
                closePopup();
                map.getTargetElement()?.focus({preventScroll: true});
                event.preventDefault();
            }
        }, {signal: events.signal});
        mobile.addEventListener('change', schedule, {signal: events.signal});

        const observer = new ResizeObserver(schedule);
        observer.observe(viewport);
        observer.observe(popupElement);
        const mapListeners = [
            map.on('postrender', schedule),
            map.on('change:size', schedule),
            map.on('change:target', () => {
                if (!map.getTargetElement()) destroy();
            })
        ];

        function destroy() {
            if (destroyed) return;
            reset();
            destroyed = true;
            if (frame !== null) cancelAnimationFrame(frame);
            frame = null;
            events.abort();
            observer.disconnect();
            ol.Observable.unByKey(mapListeners);
            svg.remove();
        }

        return {
            reset,
            focus() {
                popupElement.querySelector('.popup-close-btn')?.focus({preventScroll: true});
            },
            destroy
        };
    };
})();
