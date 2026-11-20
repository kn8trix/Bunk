/* ============================================================================
   Bunk — CareerPilot front-end
   ----------------------------------------------------------------------------
   Vanilla JS for the six career surfaces. One file, dispatched by
   `body[data-page]` so every page shares the fetch/toast/render helpers.
   All endpoints are same-origin JSON; a 401 means "sign in again".
   ========================================================================== */
(function () {
    'use strict';

    var CONFIG = window.CP_CONFIG || { urls: {} };
    var URLS = CONFIG.urls || {};
    var PAGE = document.body.getAttribute('data-page');

    /* ---------------------------------------------------------------------
       Helpers
    --------------------------------------------------------------------- */
    function $(sel, root) { return (root || document).querySelector(sel); }
    function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

    function esc(value) {
        var div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    var toastTimer = null;
    function toast(message, isError) {
        var el = document.getElementById('cp-toast');
        if (!el) return;
        el.textContent = message;
        el.classList.toggle('is-error', !!isError);
        el.classList.add('is-visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.classList.remove('is-visible'); }, 3200);
    }

    function api(url, options) {
        options = options || {};
        var init = { method: options.method || 'GET', headers: {}, credentials: 'same-origin' };

        // Get CSRF token from meta tag
        var csrfToken = document.querySelector('meta[name="csrftoken"]');
        if (csrfToken) {
            init.headers['X-CSRFToken'] = csrfToken.content;
        }

        if (options.body instanceof FormData) {
            init.body = options.body;
        } else if (options.body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(options.body);
        }
        return fetch(url, init).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (response.status === 401) {
                    throw new Error('Please sign in to use this feature.');
                }
                if (!response.ok) {
                    throw new Error(data.error || ('Request failed (' + response.status + ')'));
                }
                return data;
            });
        });
    }

    function empty(icon, text) {
        return '<div class="cp-empty"><i class="fa-solid ' + icon + '"></i> ' + esc(text) + '</div>';
    }

    function badge(text, tone) {
        return '<span class="cp-badge' + (tone ? ' cp-badge--' + tone : '') + '">' + esc(text) + '</span>';
    }

    function fmtDate(iso) {
        if (!iso) return '';
        var d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
        if (isNaN(d)) return iso;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function isoDay(date) {
        return date.getFullYear() + '-' +
            String(date.getMonth() + 1).padStart(2, '0') + '-' +
            String(date.getDate()).padStart(2, '0');
    }

    /* ---------------------------------------------------------------------
       My CV
    --------------------------------------------------------------------- */
    var CV = {
        file: null,

        init: function () {
            var drop = $('#cp-cv-drop');
            var input = $('#cp-cv-file');
            var uploadBtn = $('#cp-cv-upload-btn');
            if (!drop || !input) return;

            drop.addEventListener('click', function () { input.click(); });
            drop.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); input.click(); }
            });
            ['dragenter', 'dragover'].forEach(function (name) {
                drop.addEventListener(name, function (event) {
                    event.preventDefault(); drop.classList.add('is-over');
                });
            });
            ['dragleave', 'drop'].forEach(function (name) {
                drop.addEventListener(name, function (event) {
                    event.preventDefault(); drop.classList.remove('is-over');
                });
            });
            drop.addEventListener('drop', function (event) {
                if (event.dataTransfer.files.length) { CV.pick(event.dataTransfer.files[0]); }
            });
            input.addEventListener('change', function () {
                if (input.files.length) { CV.pick(input.files[0]); }
            });

            uploadBtn.addEventListener('click', CV.upload);
            var refresh = $('#cp-cv-refresh');
            if (refresh) refresh.addEventListener('click', function () { CV.load(); });

            CV.load();
        },

        pick: function (file) {
            CV.file = file;
            var label = $('#cp-cv-file-label');
            if (label) label.textContent = file.name + ' · ' + Math.round(file.size / 1024) + ' KB';
            var btn = $('#cp-cv-upload-btn');
            if (btn) btn.disabled = false;
        },

        upload: function () {
            if (!CV.file) return;
            var form = new FormData();
            form.append('file', CV.file);
            var nameInput = $('#cp-cv-name');
            if (nameInput && nameInput.value.trim()) form.append('name', nameInput.value.trim());

            var btn = $('#cp-cv-upload-btn');
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Indexing…';

            api(URLS.cvUpload, { method: 'POST', body: form })
                .then(function (data) {
                    toast('Indexed ' + data.chunks_created + ' chunks.');
                    CV.file = null;
                    var label = $('#cp-cv-file-label');
                    if (label) label.textContent = 'No file selected';
                    if (nameInput) nameInput.value = '';
                    CV.load();
                })
                .catch(function (error) { toast(error.message, true); })
                .finally(function () {
                    btn.disabled = !CV.file;
                    btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Upload &amp; index';
                });
        },

        load: function () {
            api(URLS.cvList).then(function (data) {
                var list = $('#cp-cv-list');
                var count = $('#cp-cv-count');
                if (count) count.textContent = data.count;
                if (!data.count) {
                    list.innerHTML = empty('fa-regular fa-folder', 'No CVs yet — upload one to get started.');
                    return;
                }
                list.innerHTML = data.cvs.map(function (cv) {
                    var tone = cv.status === 'ready' ? 'green' : (cv.status === 'failed' ? 'red' : 'amber');
                    var actions = [];
                    if (cv.is_active) actions.push(badge('Active', 'amber'));
                    else actions.push('<button type="button" class="cp-btn cp-btn--ghost cp-btn--sm" data-activate="' + cv.id + '">Activate</button>');
                    actions.push('<button type="button" class="cp-btn cp-btn--soft cp-btn--sm" data-inspect="' + cv.id + '">Inspect</button>');
                    actions.push('<button type="button" class="cp-icon-btn" data-delete="' + cv.id + '" aria-label="Delete CV"><i class="fa-solid fa-trash"></i></button>');

                    return '<div class="cp-row">' +
                        '<div class="cp-row-body">' +
                            '<div class="cp-row-title">' + esc(cv.name || 'CV') +
                                ' ' + badge(cv.status, tone) +
                                (cv.source === 'builder' ? ' ' + badge('builder', 'violet') : '') +
                            '</div>' +
                            '<div class="cp-row-meta">' + cv.chunk_count + ' chunks · ' + cv.text_chars +
                                ' chars · added ' + fmtDate(cv.created_at) + '</div>' +
                        '</div>' +
                        '<div class="cp-spread">' + actions.join('') + '</div>' +
                    '</div>';
                }).join('');

                $$('[data-inspect]', list).forEach(function (btn) {
                    btn.addEventListener('click', function () { CV.inspect(btn.getAttribute('data-inspect')); });
                });
                $$('[data-activate]', list).forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        api(URLS.cvList + btn.getAttribute('data-activate') + '/activate/', { method: 'POST' })
                            .then(function () { toast('Active CV updated.'); CV.load(); })
                            .catch(function (error) { toast(error.message, true); });
                    });
                });
                $$('[data-delete]', list).forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        var id = btn.getAttribute('data-delete');
                        if (!window.confirm('Delete this CV and all of its chunks?')) return;
                        api(URLS.cvList + id + '/', { method: 'DELETE' })
                            .then(function () { toast('CV deleted.'); CV.load(); })
                            .catch(function (error) { toast(error.message, true); });
                    });
                });
            }).catch(function (error) {
                var list = $('#cp-cv-list');
                if (list) list.innerHTML = empty('fa-solid fa-triangle-exclamation', error.message);
            });
        },

        inspect: function (id) {
            var panel = $('#cp-chunks');
            panel.innerHTML = empty('fa-solid fa-spinner', 'Loading chunks…');
            api(URLS.cvList + id + '/chunks/').then(function (data) {
                var meta = $('#cp-chunk-meta');
                if (meta) meta.textContent = data.count + ' chunks';
                if (!data.count) {
                    panel.innerHTML = empty('fa-solid fa-scissors', 'This CV has no chunks.');
                    return;
                }
                panel.innerHTML = '<div class="cp-list">' + data.chunks.map(function (chunk) {
                    return '<div class="cp-row" style="align-items:flex-start;">' +
                        '<div class="cp-row-body">' +
                            '<div class="cp-row-title">' + esc(chunk.section_label) + ' ' +
                                badge(chunk.section, 'blue') + ' ' +
                                '<span class="cp-faint">' + chunk.token_count + ' tokens · ' +
                                chunk.embedding_dimensions + 'd</span></div>' +
                            '<div class="cp-row-meta" style="white-space:pre-wrap; margin-top:.3rem;">' +
                                esc(chunk.content.slice(0, 420)) + '</div>' +
                        '</div>' +
                    '</div>';
                }).join('') + '</div>';
                panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }).catch(function (error) {
                panel.innerHTML = empty('fa-solid fa-triangle-exclamation', error.message);
            });
        }
    };

    /* ---------------------------------------------------------------------
       Job Hunter
    --------------------------------------------------------------------- */
    var HUNT = {
        init: function () {
            var btn = $('#cp-hunt-btn');
            var input = $('#cp-hunt-q');
            if (!btn) return;

            btn.addEventListener('click', function () { HUNT.search(); });
            input.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') { event.preventDefault(); HUNT.search(); }
            });
            $$('[data-q]').forEach(function (chip) {
                chip.addEventListener('click', function () {
                    input.value = chip.getAttribute('data-q');
                    HUNT.search();
                });
            });
            $$('[data-hunt-tab]').forEach(function (tab) {
                tab.addEventListener('click', function () {
                    var which = tab.getAttribute('data-hunt-tab');
                    $$('[data-hunt-tab]').forEach(function (other) {
                        other.classList.toggle('is-active', other === tab);
                    });
                    $('#cp-hunt-panel').classList.toggle('cp-hide', which !== 'search');
                    $('#cp-hunt-results-card').classList.toggle('cp-hide', which !== 'search');
                    $('#cp-hunt-saved-card').classList.toggle('cp-hide', which !== 'saved');
                    if (which === 'saved') HUNT.loadSaved();
                });
            });
            HUNT.loadSaved(true);
        },

        search: function () {
            var query = ($('#cp-hunt-q').value || '').trim();
            if (!query) { toast('Type a role or role + location first.', true); return; }

            var results = $('#cp-hunt-results');
            var status = $('#cp-hunt-status');
            var btn = $('#cp-hunt-btn');
            status.textContent = 'Hunting…';
            btn.disabled = true;
            results.innerHTML = empty('fa-solid fa-satellite-dish', 'Fanning out across job boards…');

            api(URLS.hunt + '?q=' + encodeURIComponent(query)).then(function (data) {
                status.textContent = data.cached ? 'Cached result' : 'Fresh hunt';
                $('#cp-hunt-count').textContent = data.counts.total + ' roles · ' +
                    data.counts.remote + ' remote';
                if (!data.jobs.length) {
                    results.innerHTML = empty('fa-solid fa-magnifying-glass', 'No roles matched. Try a broader role name.');
                    return;
                }
                results.innerHTML = data.jobs.map(HUNT.render).join('');
                HUNT.wire(results);
            }).catch(function (error) {
                status.textContent = 'Failed';
                results.innerHTML = empty('fa-solid fa-triangle-exclamation', error.message);
            }).finally(function () { btn.disabled = false; });
        },

        render: function (job) {
            var score = job.fit_score;
            var scoreBadge = (score == null)
                ? badge('upload a CV', 'violet')
                : badge(score + '/100 fit', score >= 65 ? 'green' : (score >= 45 ? 'amber' : 'red'));
            var highlights = (job.match_highlights || []).map(function (label) {
                return badge(label, 'green');
            }).join(' ');
            var concerns = (job.concerns || []).map(function (label) {
                return badge(label, 'red');
            }).join(' ');
            var payload = esc(JSON.stringify({
                url: job.url, title: job.title, company: job.company,
                location: job.location, salary: job.salary,
                snippet: job.snippet, source: job.source,
                job_type: job.job_type, fit_score: job.fit_score,
                fit_reason: job.fit_reason,
                match_highlights: job.match_highlights,
                concerns: job.concerns, breakdown: job.breakdown
            }));

            return '<article class="cp-job">' +
                '<div class="cp-job-top">' +
                    '<div>' +
                        '<h3 class="cp-job-title">' + esc(job.title) + '</h3>' +
                        '<div class="cp-job-company">' + esc(job.company) +
                            (job.location ? ' · ' + esc(job.location) : '') + '</div>' +
                    '</div>' +
                    '<div>' + scoreBadge + '</div>' +
                '</div>' +
                (job.snippet ? '<p class="cp-job-snippet">' + esc(job.snippet.slice(0, 240)) + '</p>' : '') +
                '<div class="cp-job-meta">' +
                    (job.salary ? badge(job.salary, 'amber') : '') +
                    (job.job_type ? badge(job.job_type, 'blue') : '') +
                    (job.source ? badge(job.source, 'violet') : '') +
                    (job.posted_at ? '<span class="cp-faint">' + fmtDate(job.posted_at) + '</span>' : '') +
                '</div>' +
                ((highlights || concerns) ? '<div class="cp-job-meta">' + highlights + ' ' + concerns + '</div>' : '') +
                '<div class="cp-job-actions">' +
                    (job.url ? '<a class="cp-btn cp-btn--ghost cp-btn--sm" href="' + esc(job.url) +
                        '" target="_blank" rel="noopener noreferrer"><i class="fa-solid fa-arrow-up-right-from-square"></i> Open</a>' : '') +
                    '<button type="button" class="cp-btn cp-btn--soft cp-btn--sm" data-save=\'' + payload + '\'>' +
                        '<i class="fa-regular fa-bookmark"></i> Save</button>' +
                    '<button type="button" class="cp-btn cp-btn--sm" data-track=\'' + payload + '\'>' +
                        '<i class="fa-solid fa-plus"></i> Track</button>' +
                '</div>' +
            '</article>';
        },

        wire: function (root) {
            $$('[data-save]', root).forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var job = JSON.parse(btn.getAttribute('data-save'));
                    api(URLS.huntSave, { method: 'POST', body: job })
                        .then(function (data) {
                            toast(data.created ? 'Saved to your list.' : 'Already saved.');
                            HUNT.loadSaved(true);
                        })
                        .catch(function (error) { toast(error.message, true); });
                });
            });
            $$('[data-track]', root).forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var job = JSON.parse(btn.getAttribute('data-track'));
                    api(URLS.applications, {
                        method: 'POST',
                        body: { company: job.company, role: job.title, url: job.url,
                                location: job.location, salary: job.salary }
                    }).then(function () {
                        toast('Added to your tracker.');
                    }).catch(function (error) { toast(error.message, true); });
                });
            });
        },

        loadSaved: function (quiet) {
            api(URLS.huntSaved).then(function (data) {
                var count = $('#cp-hunt-saved-count');
                if (count) count.textContent = data.count;
                var panel = $('#cp-hunt-saved');
                if (!panel) return;
                if (!data.count) {
                    panel.innerHTML = empty('fa-regular fa-bookmark', 'Nothing saved yet.');
                    return;
                }
                panel.innerHTML = data.jobs.map(function (job) {
                    return '<article class="cp-job">' +
                        '<div class="cp-job-top"><div>' +
                            '<h3 class="cp-job-title">' + esc(job.title) + '</h3>' +
                            '<div class="cp-job-company">' + esc(job.company) + '</div>' +
                        '</div><div>' +
                            (job.fit_score != null ? badge(job.fit_score + '/100 fit', 'green') : '') +
                        '</div></div>' +
                        '<div class="cp-job-actions">' +
                            (job.url ? '<a class="cp-btn cp-btn--ghost cp-btn--sm" href="' + esc(job.url) +
                                '" target="_blank" rel="noopener noreferrer">Open</a>' : '') +
                            '<button type="button" class="cp-btn cp-btn--ghost cp-btn--sm" data-unsave="' + job.id + '">' +
                                '<i class="fa-solid fa-trash"></i> Remove</button>' +
                        '</div>' +
                    '</article>';
                }).join('');
                $$('[data-unsave]', panel).forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        api(URLS.huntSaved + btn.getAttribute('data-unsave') + '/', { method: 'DELETE' })
                            .then(function () { HUNT.loadSaved(true); })
                            .catch(function (error) { toast(error.message, true); });
                    });
                });
            }).catch(function (error) { if (!quiet) toast(error.message, true); });
        }
    };

    /* ---------------------------------------------------------------------
       Fit Score
    --------------------------------------------------------------------- */
    var FIT = {
        init: function () {
            var btn = $('#cp-fit-run');
            if (!btn) return;
            btn.addEventListener('click', function () { FIT.run(btn); });
        },

        run: function (btn) {
            var jd = ($('#cp-fit-jd').value || '').trim();
            var role = ($('#cp-fit-role').value || '').trim();
            if (!jd && !role) { toast('Paste a job description or name a target role.', true); return; }

            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Scoring…';
            $('#cp-fit-status').textContent = 'Computing…';

            api(URLS.fit, { method: 'POST', body: { jd: jd, target_role: role } })
                .then(function (data) {
                    $('#cp-fit-status').textContent = data.cached ? 'Stored result' : 'New score';
                    FIT.render(data.result);
                })
                .catch(function (error) {
                    $('#cp-fit-status').textContent = 'Failed';
                    $('#cp-fit-result').innerHTML = empty('fa-solid fa-triangle-exclamation', error.message);
                })
                .finally(function () {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-gauge-high"></i> Compute fit';
                });
        },

        render: function (result) {
            var tone = result.verdict === 'strong' ? 'green'
                : result.verdict === 'good' ? 'amber'
                : result.verdict === 'borderline' ? 'blue' : 'red';
            var must = Math.round((result.breakdown.must_have_coverage || 0) * 100);
            var nice = Math.round((result.breakdown.nice_to_have_coverage || 0) * 100);

            function skillChips(items, cls) {
                if (!items || !items.length) return '<span class="cp-faint">None</span>';
                return items.map(function (item) { return badge(item.label, cls); }).join(' ');
            }

            $('#cp-fit-result').innerHTML =
                '<div class="cp-score">' +
                    '<div class="cp-ring" style="--pct:' + result.score + ';">' +
                        '<div class="cp-ring-inner">' +
                            '<div class="cp-ring-score">' + result.score + '</div>' +
                            '<div class="cp-ring-label">/ 100</div>' +
                        '</div>' +
                    '</div>' +
                    '<div style="flex:1; min-width: 180px;">' +
                        '<div class="cp-spread" style="margin-bottom:.5rem;">' +
                            badge(result.verdict, tone) +
                            badge(result.benchmark_used, 'violet') +
                        '</div>' +
                        '<p class="cp-muted" style="margin:0 0 .6rem;">' + esc(result.rationale) + '</p>' +
                        '<div class="cp-label">Must-have coverage · ' + must + '%</div>' +
                        '<div class="cp-bar"><span style="width:' + must + '%"></span></div>' +
                        '<div class="cp-label cp-mt-sm">Nice-to-have coverage · ' + nice + '%</div>' +
                        '<div class="cp-bar"><span style="width:' + nice + '%"></span></div>' +
                    '</div>' +
                '</div>' +
                '<div class="cp-mt">' +
                    '<div class="cp-label">Strengths</div>' +
                    '<div class="cp-skill-list">' + skillChips(result.matched, 'green') + '</div>' +
                '</div>' +
                '<div class="cp-mt">' +
                    '<div class="cp-label">Missing must-haves</div>' +
                    '<div class="cp-skill-list">' + skillChips(result.missing, 'red') + '</div>' +
                '</div>' +
                (result.missing_nice_to_have && result.missing_nice_to_have.length
                    ? '<div class="cp-mt"><div class="cp-label">Bonus skills to add</div>' +
                      '<div class="cp-skill-list">' + skillChips(result.missing_nice_to_have, 'amber') + '</div></div>'
                    : '');
        }
    };

    /* ---------------------------------------------------------------------
       Assistant
    --------------------------------------------------------------------- */
    var CHAT = {
        threadId: null,
        mode: 'general',

        init: function () {
            var send = $('#cp-chat-send');
            if (!send) return;

            send.addEventListener('click', function () { CHAT.send(); });
            $('#cp-chat-input').addEventListener('keydown', function (event) {
                if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); CHAT.send(); }
            });
            $('#cp-chat-new').addEventListener('click', function () { CHAT.reset(); });

            $$('[data-mode]').forEach(function (chip) {
                chip.addEventListener('click', function () {
                    CHAT.mode = chip.getAttribute('data-mode');
                    $$('[data-mode]').forEach(function (other) {
                        other.classList.toggle('is-active', other === chip);
                    });
                });
            });

            CHAT.loadThreads();
        },

        reset: function () {
            CHAT.threadId = null;
            $('#cp-chat-title').textContent = 'New chat';
            $('#cp-chat-messages').innerHTML = empty(
                'fa-solid fa-wand-magic-sparkles',
                'Ask anything — “Am I ready for a backend role?”, “What are my gaps?”'
            );
            CHAT.loadThreads();
        },

        loadThreads: function () {
            api(URLS.threads).then(function (data) {
                var panel = $('#cp-chat-threads');
                if (!data.count) {
                    panel.innerHTML = empty('fa-regular fa-comments', 'No conversations yet.');
                    return;
                }
                panel.innerHTML = data.threads.map(function (thread) {
                    return '<button type="button" class="cp-thread' +
                        (thread.id === CHAT.threadId ? ' is-active' : '') +
                        '" data-thread="' + thread.id + '">' +
                        esc(thread.title) +
                        '<small>' + thread.message_count + ' messages · ' + fmtDate(thread.updated_at) + '</small>' +
                    '</button>';
                }).join('');
                $$('[data-thread]', panel).forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        CHAT.open(btn.getAttribute('data-thread'));
                    });
                });
            }).catch(function () { /* sidebar is optional; chat still works */ });
        },

        open: function (id) {
            CHAT.threadId = Number(id);
            api(URLS.threads + id + '/').then(function (data) {
                $('#cp-chat-title').textContent = data.thread.title;
                CHAT.render(data.messages);
                CHAT.loadThreads();
            }).catch(function (error) { toast(error.message, true); });
        },

        render: function (messages) {
            var panel = $('#cp-chat-messages');
            if (!messages.length) {
                panel.innerHTML = empty('fa-solid fa-wand-magic-sparkles', 'Start the conversation below.');
                return;
            }
            panel.innerHTML = messages.map(function (message) {
                if (message.role === 'user') {
                    return '<div class="cp-msg cp-msg--user">' + esc(message.content) + '</div>';
                }
                var cites = (message.citations || []).map(function (cite) {
                    return '<div class="cp-cite"><strong>' + esc(cite.section_label) +
                        '</strong>' + esc(cite.excerpt.slice(0, 220)) + '</div>';
                }).join('');
                return '<div class="cp-msg cp-msg--model">' +
                    '<div class="cp-msg-head">' + badge(message.mode || 'general', 'amber') + '</div>' +
                    esc(message.content) +
                    (cites ? '<div class="cp-cites">' + cites + '</div>' : '') +
                '</div>';
            }).join('');
            panel.scrollTop = panel.scrollHeight;
        },

        send: function () {
            var input = $('#cp-chat-input');
            var text = (input.value || '').trim();
            if (!text) return;
            var role = ($('#cp-chat-role').value || '').trim();
            var body = { message: text, mode: CHAT.mode };
            if (role) body.message = text + '\n\nTarget role: ' + role;

            input.value = '';
            var btn = $('#cp-chat-send');
            btn.disabled = true;

            var request = CHAT.threadId
                ? api(URLS.threads + CHAT.threadId + '/messages/', { method: 'POST', body: body })
                    .then(function (data) {
                        $('#cp-chat-messages').insertAdjacentHTML(
                            'beforeend',
                            '<div class="cp-msg cp-msg--user">' + esc(text) + '</div>' +
                            CHAT.renderOne(data.model)
                        );
                        var panel = $('#cp-chat-messages');
                        panel.scrollTop = panel.scrollHeight;
                        $('#cp-chat-engine').textContent = data.engine || '';
                    })
                : api(URLS.threads, { method: 'POST', body: body })
                    .then(function (data) {
                        CHAT.threadId = data.thread.id;
                        $('#cp-chat-title').textContent = data.thread.title;
                        CHAT.render([
                            { role: 'user', content: text },
                            data.messages[0].model
                        ]);
                        CHAT.loadThreads();
                    });

            request.catch(function (error) {
                toast(error.message, true);
            }).finally(function () { btn.disabled = false; });
        },

        renderOne: function (message) {
            var cites = (message.citations || []).map(function (cite) {
                return '<div class="cp-cite"><strong>' + esc(cite.section_label) +
                    '</strong>' + esc(cite.excerpt.slice(0, 220)) + '</div>';
            }).join('');
            return '<div class="cp-msg cp-msg--model">' +
                '<div class="cp-msg-head">' + badge(message.mode || 'general', 'amber') + '</div>' +
                esc(message.content) +
                (cites ? '<div class="cp-cites">' + cites + '</div>' : '') +
            '</div>';
        }
    };

    /* ---------------------------------------------------------------------
       Tracker
    --------------------------------------------------------------------- */
    var TRACKER = {
        statuses: ['applied', 'interviewing', 'offer', 'rejected'],
        loaded: [],

        init: function () {
            var toggle = $('#cp-app-toggle');
            if (!toggle) return;

            toggle.addEventListener('click', function () {
                $('#cp-app-form-card').classList.toggle('cp-hide');
            });
            $('#cp-app-cancel').addEventListener('click', function () {
                $('#cp-app-form-card').classList.add('cp-hide');
            });
            $('#cp-app-save').addEventListener('click', function () { TRACKER.save(); });

            TRACKER.load();
            TRACKER.loadStats();
        },

        loadStats: function () {
            api(URLS.appStats).then(function (data) {
                $('#cp-stat-apps').textContent = data.applications_count;
                $('#cp-stat-saved').textContent = data.saved_jobs_count;
                $('#cp-stat-streak').textContent = data.streak_days;
                $('#cp-stat-todos').textContent = data.open_todos;
            }).catch(function () { /* cards keep their placeholder */ });
        },

        load: function () {
            api(URLS.applications).then(function (data) {
                TRACKER.loaded = data.applications;
                TRACKER.render(data.applications);
                $('#cp-app-status').textContent = data.count + ' tracked';
            }).catch(function (error) {
                $('#cp-app-status').textContent = 'Failed to load';
                toast(error.message, true);
            });
        },

        render: function (rows) {
            TRACKER.statuses.forEach(function (status) {
                var column = $('[data-col="' + status + '"]');
                var counter = $('[data-count="' + status + '"]');
                var items = rows.filter(function (row) { return row.status === status; });
                if (counter) counter.textContent = items.length;
                if (!column) return;

                if (!items.length) {
                    column.innerHTML = '<div class="cp-faint" style="font-size:.76rem;padding:.4rem;">Empty</div>';
                    return;
                }
                column.innerHTML = items.map(function (row) {
                    var previous = TRACKER.statuses[TRACKER.statuses.indexOf(status) - 1];
                    var next = TRACKER.statuses[TRACKER.statuses.indexOf(status) + 1];
                    return '<div class="cp-kcard">' +
                        '<h4>' + esc(row.role) + '</h4>' +
                        '<p>' + esc(row.company) + (row.deadline ? ' · due ' + fmtDate(row.deadline) : '') + '</p>' +
                        '<div class="cp-kcard-actions">' +
                            (previous ? '<button type="button" class="cp-icon-btn" data-move="' + row.id +
                                '" data-to="' + previous + '" aria-label="Move back"><i class="fa-solid fa-arrow-left"></i></button>' : '') +
                            (next ? '<button type="button" class="cp-icon-btn" data-move="' + row.id +
                                '" data-to="' + next + '" aria-label="Move forward"><i class="fa-solid fa-arrow-right"></i></button>' : '') +
                            '<button type="button" class="cp-icon-btn" data-drop="' + row.id +
                                '" aria-label="Delete application"><i class="fa-solid fa-trash"></i></button>' +
                        '</div>' +
                    '</div>';
                }).join('');
            });

            $$('[data-move]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var id = btn.getAttribute('data-move');
                    api(URLS.applications + id + '/', {
                        method: 'POST',
                        body: { status: btn.getAttribute('data-to') }
                    }).then(function () { TRACKER.load(); TRACKER.loadStats(); })
                      .catch(function (error) { toast(error.message, true); });
                });
            });
            $$('[data-drop]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    if (!window.confirm('Delete this application?')) return;
                    api(URLS.applications + btn.getAttribute('data-drop') + '/', { method: 'DELETE' })
                        .then(function () { TRACKER.load(); TRACKER.loadStats(); })
                        .catch(function (error) { toast(error.message, true); });
                });
            });
        },

        save: function () {
            var body = {
                company: $('#cp-app-company').value.trim(),
                role: $('#cp-app-role').value.trim(),
                url: $('#cp-app-url').value.trim(),
                deadline: $('#cp-app-deadline').value || null
            };
            if (!body.company || !body.role) {
                toast('Company and role are required.', true);
                return;
            }
            api(URLS.applications, { method: 'POST', body: body }).then(function () {
                toast('Application added.');
                $('#cp-app-company').value = '';
                $('#cp-app-role').value = '';
                $('#cp-app-url').value = '';
                $('#cp-app-deadline').value = '';
                $('#cp-app-form-card').classList.add('cp-hide');
                TRACKER.load();
                TRACKER.loadStats();
            }).catch(function (error) { toast(error.message, true); });
        }
    };

    /* ---------------------------------------------------------------------
       Calendar
    --------------------------------------------------------------------- */
    var CAL = {
        view: new Date(),
        selected: new Date(),

        init: function () {
            var grid = $('#cp-cal');
            if (!grid) return;

            $('#cp-cal-prev').addEventListener('click', function () {
                CAL.view = new Date(CAL.view.getFullYear(), CAL.view.getMonth() - 1, 1);
                CAL.renderGrid();
            });
            $('#cp-cal-next').addEventListener('click', function () {
                CAL.view = new Date(CAL.view.getFullYear(), CAL.view.getMonth() + 1, 1);
                CAL.renderGrid();
            });
            $('#cp-cal-today').addEventListener('click', function () {
                var now = new Date();
                CAL.view = new Date(now.getFullYear(), now.getMonth(), 1);
                CAL.selected = now;
                CAL.renderGrid();
                CAL.loadDay(isoDay(now));
            });
            $('#cp-todo-add').addEventListener('click', function () { CAL.addTodo(); });
            $('#cp-todo-input').addEventListener('keydown', function (event) {
                if (event.key === 'Enter') { event.preventDefault(); CAL.addTodo(); }
            });

            CAL.view = new Date(CAL.selected.getFullYear(), CAL.selected.getMonth(), 1);
            CAL.renderGrid();
            CAL.loadDay(isoDay(CAL.selected));
        },

        renderGrid: function () {
            var grid = $('#cp-cal');
            var year = CAL.view.getFullYear();
            var month = CAL.view.getMonth();
            $('#cp-cal-label').textContent = CAL.view.toLocaleDateString(undefined, {
                month: 'long', year: 'numeric'
            });

            var first = new Date(year, month, 1);
            var offset = (first.getDay() + 6) % 7; // Monday-first
            var days = new Date(year, month + 1, 0).getDate();
            var todayKey = isoDay(new Date());
            var selectedKey = isoDay(CAL.selected);
            var html = '';

            for (var i = 0; i < offset; i++) {
                html += '<div class="cp-cal-day is-empty"></div>';
            }
            for (var day = 1; day <= days; day++) {
                var key = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
                var classes = 'cp-cal-day';
                if (key === todayKey) classes += ' is-today';
                if (key === selectedKey) classes += ' is-selected';
                html += '<button type="button" class="' + classes + '" data-date="' + key + '">' + day + '</button>';
            }
            grid.innerHTML = html;

            $$('[data-date]', grid).forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var parts = btn.getAttribute('data-date').split('-');
                    CAL.selected = new Date(+parts[0], +parts[1] - 1, +parts[2]);
                    CAL.renderGrid();
                    CAL.loadDay(btn.getAttribute('data-date'));
                });
            });
        },

        loadDay: function (dateKey) {
            var panel = $('#cp-day-todos');
            panel.innerHTML = empty('fa-solid fa-spinner', 'Loading…');
            api(URLS.calendar + '?date=' + encodeURIComponent(dateKey)).then(function (data) {
                var label = new Date(dateKey + 'T00:00:00');
                $('#cp-day-label').textContent = label.toLocaleDateString(undefined, {
                    weekday: 'long', month: 'short', day: 'numeric'
                });
                $('#cp-day-count').textContent = data.counts.todos;

                if (!data.todos.length) {
                    panel.innerHTML = empty('fa-regular fa-circle-check', 'Nothing scheduled for this day.');
                } else {
                    panel.innerHTML = data.todos.map(function (todo) {
                        return '<div class="cp-row">' +
                            '<button type="button" class="cp-icon-btn" data-todo-done="' + todo.id +
                                '" aria-label="Mark done"><i class="fa-solid ' +
                                (todo.done ? 'fa-circle-check' : 'fa-circle') + '"></i></button>' +
                            '<div class="cp-row-body">' +
                                '<div class="cp-row-title" style="' +
                                    (todo.done ? 'text-decoration:line-through;opacity:.6;' : '') + '">' +
                                    esc(todo.title) + '</div>' +
                            '</div>' +
                        '</div>';
                    }).join('');
                    $$('[data-todo-done]', panel).forEach(function (btn) {
                        btn.addEventListener('click', function () {
                            api(URLS.todos + btn.getAttribute('data-todo-done') + '/done/', { method: 'POST' })
                                .then(function () { CAL.loadDay(dateKey); })
                                .catch(function (error) { toast(error.message, true); });
                        });
                    });
                }

                var deadlines = $('#cp-day-deadlines');
                if (data.application_deadlines.length) {
                    deadlines.innerHTML = '<div class="cp-label">Application deadlines</div>' +
                        data.application_deadlines.map(function (row) {
                            return '<div class="cp-row"><div class="cp-row-body">' +
                                '<div class="cp-row-title">' + esc(row.role) + '</div>' +
                                '<div class="cp-row-meta">' + esc(row.company) + ' · ' + badge(row.status, 'blue') + '</div>' +
                            '</div></div>';
                        }).join('');
                } else {
                    deadlines.innerHTML = '';
                }
            }).catch(function (error) {
                panel.innerHTML = empty('fa-solid fa-triangle-exclamation', error.message);
            });
        },

        addTodo: function () {
            var input = $('#cp-todo-input');
            var title = (input.value || '').trim();
            if (!title) return;
            api(URLS.todos, {
                method: 'POST',
                body: { title: title, due_date: isoDay(CAL.selected) }
            }).then(function () {
                input.value = '';
                toast('To-do added.');
                CAL.loadDay(isoDay(CAL.selected));
            }).catch(function (error) { toast(error.message, true); });
        }
    };

    /* ---------------------------------------------------------------------
       Boot
    --------------------------------------------------------------------- */
    document.addEventListener('DOMContentLoaded', function () {
        try {
            if (PAGE === 'cv') CV.init();
            else if (PAGE === 'hunter') HUNT.init();
            else if (PAGE === 'fit-score') FIT.init();
            else if (PAGE === 'chat') CHAT.init();
            else if (PAGE === 'tracker') TRACKER.init();
            else if (PAGE === 'calendar') CAL.init();
        } catch (error) {
            toast('This page failed to initialise: ' + error.message, true);
        }
    });
})();
