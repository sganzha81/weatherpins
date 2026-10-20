(function () {
    'use strict';

    const DISTANCE = 52; // Existing pins are about 35 × 48 CSS pixels.

    function dateLabel(feature) {
        const value = feature.get('taken_at_display');
        return value ? value.replace(' ', ', ') : 'Дата съёмки неизвестна';
    }

    function dateKey(feature) {
        // Compare the server's wall-clock representation without timezone conversion.
        const value = feature.get('taken_at_display') || '';
        const match = /^(\d{2})\.(\d{2})\.(\d{4})(?: (\d{2}:\d{2}))?$/.exec(value);
        return match ? `${match[3]}${match[2]}${match[1]}${match[4] || ''}` : '';
    }

    window.createPhotoClusters = function ({map, source, photoStyle, openPhotoPopup, closePopup}) {
        const clusterSource = new ol.source.Cluster({distance: DISTANCE, source});
        const styles = new Map();
        const gallery = document.createElement('section');
        gallery.className = 'photo-cluster-gallery';
        gallery.hidden = true;
        gallery.setAttribute('aria-label', 'Фотографии в этой точке');
        map.addControl(new ol.control.Control({element: gallery}));

        function closeGallery() {
            gallery.hidden = true;
            gallery.replaceChildren();
        }

        function showGallery(features) {
            const heading = document.createElement('h2');
            heading.textContent = `${features.length} фотографий в этой точке`;
            const close = document.createElement('button');
            close.type = 'button';
            close.className = 'photo-cluster-close';
            close.textContent = '✕';
            close.setAttribute('aria-label', 'Закрыть галерею');
            close.addEventListener('click', () => {
                closeGallery();
                map.getTargetElement().focus();
            });
            const grid = document.createElement('div');
            grid.className = 'photo-cluster-grid';
            const sorted = features.slice().sort((a, b) =>
                dateKey(b).localeCompare(dateKey(a)) || Number(a.get('id')) - Number(b.get('id'))
            );
            sorted.forEach((feature, index) => {
                const label = dateLabel(feature);
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'photo-cluster-choice';
                button.title = label;
                button.setAttribute('aria-label', `Открыть фотографию ${index + 1} из ${features.length}: ${label}`);
                const image = document.createElement('img');
                image.src = feature.get('image_url') || '';
                image.alt = `Фотография: ${label}`;
                image.loading = 'lazy';
                const caption = document.createElement('span');
                caption.className = 'photo-cluster-date';
                caption.textContent = label;
                button.append(image, caption);
                button.addEventListener('click', () => {
                    closeGallery();
                    openPhotoPopup(feature);
                });
                grid.append(button);
            });
            gallery.replaceChildren(heading, close, grid);
            gallery.hidden = false;
            close.focus();
        }

        function splitZoom(features) {
            const view = map.getView();
            const currentZoom = view.getZoom();
            const maxZoom = view.getMaxZoom();
            if (currentZoom >= maxZoom - 0.001) return null;

            // Ask the same native algorithm at candidate resolutions. A coordinate
            // equality check misses nearby points that never separate on screen.
            const probeSource = new ol.source.Vector({features});
            const probe = new ol.source.Cluster({distance: DISTANCE, source: probeSource});
            const splits = zoom => {
                probe.loadFeatures(source.getExtent(), view.getResolutionForZoom(zoom), view.getProjection());
                return probe.getFeatures().length > 1;
            };
            try {
                if (!splits(maxZoom)) return null;
                for (let zoom = Math.min(Math.floor(currentZoom) + 1, maxZoom);
                    zoom < maxZoom; zoom += 1) {
                    if (splits(zoom)) return zoom;
                }
                return maxZoom;
            } finally {
                // Cluster holds a change listener on its underlying Vector source.
                probe.setSource(null);
                probe.dispose();
                probeSource.dispose();
            }
        }

        function handleClick(cluster) {
            closePopup();
            if (!cluster) return;
            const features = cluster.get('features');
            if (!features || !features.length) return;
            if (features.length === 1) {
                openPhotoPopup(features[0]);
                return;
            }
            const zoom = splitZoom(features);
            if (zoom === null) {
                showGallery(features);
            } else {
                const extent = ol.extent.createEmpty();
                features.forEach(feature => ol.extent.extend(extent, feature.getGeometry().getExtent()));
                map.getView().animate({center: ol.extent.getCenter(extent), zoom, duration: 350});
            }
        }

        map.on('movestart', closeGallery);
        source.on('removefeature', closeGallery);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !gallery.hidden) {
                closeGallery();
                map.getTargetElement().focus();
            }
        });

        return {
            source: clusterSource,
            closeGallery,
            handleClick,
            style(cluster) {
                const features = cluster.get('features');
                const count = features.length;
                if (count === 1) return photoStyle(features[0]);
                if (!styles.has(count)) {
                    styles.set(count, new ol.style.Style({
                        image: new ol.style.Circle({
                            radius: Math.max(23, String(count).length * 5 + 9),
                            fill: new ol.style.Fill({color: '#1769e8'}),
                            stroke: new ol.style.Stroke({color: '#e0f2fe', width: 3})
                        }),
                        text: new ol.style.Text({
                            text: String(count),
                            font: 'bold 15px sans-serif',
                            fill: new ol.style.Fill({color: '#ffffff'})
                        })
                    }));
                }
                return styles.get(count);
            }
        };
    };
})();
