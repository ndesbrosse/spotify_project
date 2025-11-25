window.dash_clientside = Object.assign({}, window.dash_clientside, {
    ranking: {
      // Init drag & drop pour le classement solo
      init_dnd: function(pathname, tracks) {
        try {
          if (pathname !== '/ranking') {
            return window.dash_clientside.no_update;
          }
  
          function attachDnD() {
            var list = document.getElementById('rank-list');
            if (!list) {
              window.setTimeout(attachDnD, 100);
              return;
            }
            if (list.__dnd_inited) {
              return;
            }
            list.__dnd_inited = true;
  
            var dragEl = null;
  
            list.addEventListener('dragstart', function(e) {
              var li = e.target.closest('li');
              if (!li) return;
              dragEl = li;
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', li.getAttribute('data-id'));
              li.style.opacity = '0.4';
            });
  
            list.addEventListener('dragend', function(e) {
              if (dragEl) { dragEl.style.opacity = ''; }
              dragEl = null;
            });
  
            list.addEventListener('dragover', function(e) {
              e.preventDefault();
              var over = e.target.closest('li');
              if (!over || over === dragEl) return;
              var rect = over.getBoundingClientRect();
              var next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
              list.insertBefore(dragEl, next ? over.nextSibling : over);
            });
  
            list.addEventListener('drop', function(e) {
              e.preventDefault();
            });
          }
  
          window.setTimeout(attachDnD, 0);
          return Date.now();
        } catch (e) {
          console.warn('ranking.init_dnd error', e);
          return window.dash_clientside.no_update;
        }
      },
  
      // Lire l'ordre des <li> quand on clique sur "Valider"
      read_order: function(n_clicks) {
        try {
          if (!n_clicks) {
            return window.dash_clientside.no_update;
          }
          var list = document.getElementById('rank-list');
          if (!list) {
            return window.dash_clientside.no_update;
          }
          var order = [];
          for (var i = 0; i < list.children.length; i++) {
            var id = list.children[i].getAttribute('data-id');
            if (id) order.push(id);
          }
          return order;
        } catch (e) {
          console.warn('ranking.read_order error', e);
          return window.dash_clientside.no_update;
        }
      }
    },
  
    rankingMulti: {
      // Init drag & drop pour le multi
      init_dnd: function(pathname, gameCode) {
        try {
          if (pathname !== '/ranking-multi' || !gameCode) {
            return window.dash_clientside.no_update;
          }
  
          function attachDnD() {
            var list = document.getElementById('mp-rank-list');
            if (!list) {
              window.setTimeout(attachDnD, 100);
              return;
            }
            if (list.__dnd_inited) {
              return;
            }
            list.__dnd_inited = true;
  
            var dragEl = null;
  
            list.addEventListener('dragstart', function(e) {
              var li = e.target.closest('li');
              if (!li) return;
              dragEl = li;
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', li.getAttribute('data-id'));
              li.style.opacity = '0.4';
            });
  
            list.addEventListener('dragend', function(e) {
              if (dragEl) { dragEl.style.opacity = ''; }
              dragEl = null;
            });
  
            list.addEventListener('dragover', function(e) {
              e.preventDefault();
              var over = e.target.closest('li');
              if (!over || over === dragEl) return;
              var rect = over.getBoundingClientRect();
              var next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
              list.insertBefore(dragEl, next ? over.nextSibling : over);
            });
  
            list.addEventListener('drop', function(e) {
              e.preventDefault();
            });
          }
  
          window.setTimeout(attachDnD, 0);
          return Date.now();
        } catch (e) {
          console.warn('rankingMulti.init_dnd error', e);
          return window.dash_clientside.no_update;
        }
      },
  
      // Lire l'ordre au clic sur "Valider mon classement"
      read_order: function(n_clicks) {
        try {
          if (!n_clicks) {
            return window.dash_clientside.no_update;
          }
          var list = document.getElementById('mp-rank-list');
          if (!list) {
            return window.dash_clientside.no_update;
          }
          var order = [];
          for (var i = 0; i < list.children.length; i++) {
            var id = list.children[i].getAttribute('data-id');
            if (id) order.push(id);
          }
          return order;
        } catch (e) {
          console.warn('rankingMulti.read_order error', e);
          return window.dash_clientside.no_update;
        }
      },
  
      // Snapshot régulier pour le cas où le joueur ne valide pas
      snapshot_order: function(n_intervals) {
        try {
          if (!n_intervals) {
            return window.dash_clientside.no_update;
          }
          var list = document.getElementById('mp-rank-list');
          if (!list) {
            return window.dash_clientside.no_update;
          }
          var order = [];
          for (var i = 0; i < list.children.length; i++) {
            var id = list.children[i].getAttribute('data-id');
            if (id) order.push(id);
          }
          return order;
        } catch (e) {
          console.warn('rankingMulti.snapshot_order error', e);
          return window.dash_clientside.no_update;
        }
      }
    }
  });
  