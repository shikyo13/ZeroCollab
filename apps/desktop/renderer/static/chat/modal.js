/* ZeroCollab chat - Full-size image viewer with previous/next across every image in the chat.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Image modal ---

let modalImages = [];  // all image URLs in chat
let modalIndex = 0;    // current image index

function getAllChatImages() {
    const imgs = document.querySelectorAll('.msg-attachments img');
    return [...imgs].map(img => img.src);
}

function openImageModal(url) {
    if (window.ZeroImageReview) {
        const nodes = [...document.querySelectorAll('.msg-attachments img')];
        let images = nodes.map(img => ({ url: img.src, name: img.alt || 'Shared image',
            note: img.dataset.note || img.closest('.message')?.querySelector('.msg-text')?.textContent || '',
            source: img.dataset.source || undefined }));
        const href = new URL(url, location.href).href;
        let index = images.findIndex(image => image.url === href);
        if (index < 0) { images = [pendingAttachments.find(a => new URL(a.url, location.href).href === href) || { url, name: 'Image preview' }]; index = 0; }
        window.ZeroImageReview(images, index, attachment => {
            pendingAttachments.push(attachment);
            renderAttachments(); updateSendButton(); document.getElementById('input').focus();
        });
        return;
    }
    modalImages = getAllChatImages();
    // Match by endsWith since onclick passes relative URL but img.src is absolute
    modalIndex = modalImages.findIndex(src => src.endsWith(url) || src === url);
    if (modalIndex === -1) {
        // Image not in chat gallery (e.g. composer preview) — show it standalone
        modalImages = [url];
        modalIndex = 0;
    }

    let modal = document.getElementById('image-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'image-modal';
        modal.className = 'hidden';
        modal.innerHTML = `<button class="modal-nav modal-prev" onclick="modalPrev(event)">&lsaquo;</button><img onclick="event.stopPropagation()"><button class="modal-nav modal-next" onclick="modalNext(event)">&rsaquo;</button><span class="modal-counter"></span>`;
        modal.addEventListener('click', closeImageModal);
        const tools = document.createElement('div');
        tools.className = 'image-modal-tools';
        tools.addEventListener('click', event => event.stopPropagation());
        const annotate = document.createElement('button');
        annotate.textContent = 'Annotate a copy';
        annotate.className = 'settings-btn';
        annotate.onclick = () => {
            const src = modalImages[modalIndex];
            closeImageModal();
            window.ZeroAnnotate?.(src, attachment => {
                pendingAttachments.push(attachment);
                renderAttachments();
                updateSendButton();
                document.getElementById('input').focus();
            });
        };
        const close = document.createElement('button');
        close.textContent = 'Close ✕';
        close.className = 'settings-btn';
        close.onclick = closeImageModal;
        tools.append(annotate, close);
        modal.append(tools);
        const notes = document.createElement('details');
        notes.className = 'image-modal-notes';
        notes.addEventListener('click', event => event.stopPropagation());
        const summary = document.createElement('summary');
        summary.textContent = 'Image notes / message';
        const body = document.createElement('p');
        notes.append(summary, body);
        modal.append(notes);
        document.body.appendChild(modal);
    }
    updateModalImage(modal);
    modal.classList.remove('hidden');
}

function updateModalImage(modal) {
    if (!modal) modal = document.getElementById('image-modal');
    if (!modal || modalImages.length === 0) return;
    modal.querySelector('img').src = modalImages[modalIndex];
    const imageUrl = new URL(modalImages[modalIndex], location.href);
    const sourceImage = [...document.querySelectorAll('.msg-attachments img')].find(img => img.src === imageUrl.href);
    const draft = pendingAttachments.find(a => new URL(a.url, location.href).href === imageUrl.href);
    const noteText = draft?.note || sourceImage?.dataset.note || sourceImage?.closest('.message')?.querySelector('.msg-text')?.textContent || '';
    const notes = modal.querySelector('.image-modal-notes');
    if (notes) { notes.hidden = !noteText; notes.querySelector('p').textContent = noteText; }
    const annotate = modal.querySelector('.image-modal-tools button');
    if (annotate) annotate.hidden = !window.ZeroAnnotate || imageUrl.origin !== location.origin || !imageUrl.pathname.startsWith('/uploads/') || !/\.(png|jpe?g|gif|webp)$/i.test(imageUrl.pathname);
    const counter = modal.querySelector('.modal-counter');
    if (counter) {
        counter.textContent = `${modalIndex + 1} / ${modalImages.length}`;
    }
    // Hide arrows at beginning/end, or if only one image
    const prev = modal.querySelector('.modal-prev');
    const next = modal.querySelector('.modal-next');
    if (prev) prev.hidden = modalIndex === 0;
    if (next) next.hidden = modalIndex >= modalImages.length - 1;
}

function modalPrev(event) {
    event.stopPropagation();
    if (modalIndex <= 0) return;
    modalIndex--;
    updateModalImage();
}

function modalNext(event) {
    event.stopPropagation();
    if (modalIndex >= modalImages.length - 1) return;
    modalIndex++;
    updateModalImage();
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    if (modal) modal.classList.add('hidden');
}
