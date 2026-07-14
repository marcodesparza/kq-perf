import { onMounted, onPatched, useEffect, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { ListRenderer } from "@web/views/list/list_renderer";

const OVERSCAN_ROWS = 15;
const INITIAL_WINDOW = 100;
const DEFAULT_ROW_HEIGHT = 34;

patch(ListRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        this.virtualThreshold = session.kq_list_virtual_threshold ?? 0;
        this.virtualRowHeight = DEFAULT_ROW_HEIGHT;
        this.virtualState = useState({ start: 0, end: INITIAL_WINDOW });
        if (!this.virtualThreshold) {
            return;
        }
        const onScroll = () => this._scheduleVirtualUpdate();
        useEffect(
            (rootEl) => {
                if (!rootEl) {
                    return;
                }
                const scrollEl = this._getVirtualScrollElement();
                if (!scrollEl) {
                    return;
                }
                scrollEl.addEventListener("scroll", onScroll);
                return () => scrollEl.removeEventListener("scroll", onScroll);
            },
            () => [this.rootRef.el]
        );
        onMounted(() => this._afterVirtualRender());
        onPatched(() => this._afterVirtualRender());
    },

    shouldVirtualize(list) {
        return Boolean(
            this.virtualThreshold &&
                list === this.props.list &&
                !list.isGrouped &&
                !this.props.editable &&
                !this.isX2Many &&
                list.records.length > this.virtualThreshold
        );
    },

    computeAggregates() {
        if (
            this.aggregates &&
            this.shouldVirtualize(this.props.list) &&
            (this._kqScrollRender || this.props.list._kqChunksPending)
        ) {
            return this.aggregates;
        }
        return super.computeAggregates(...arguments);
    },

    get selectAll() {
        if (
            this._kqSelectAll !== undefined &&
            this._kqScrollRender &&
            this.shouldVirtualize(this.props.list)
        ) {
            return this._kqSelectAll;
        }
        this._kqSelectAll = super.selectAll;
        return this._kqSelectAll;
    },

    getVirtualRange(list) {
        const total = list.records.length;
        let start = Math.max(0, Math.min(this.virtualState.start, total - 1));
        let end = Math.min(Math.max(this.virtualState.end, start + 1), total);
        return { start, end };
    },

    getVirtualRecords(list) {
        if (!this.shouldVirtualize(list)) {
            return list.records;
        }
        const { start, end } = this.getVirtualRange(list);
        return list.records.slice(start, end);
    },

    getVirtualSpacerTop(list) {
        const { start } = this.getVirtualRange(list);
        return Math.round(start * this.virtualRowHeight);
    },

    getVirtualSpacerBottom(list) {
        const { end } = this.getVirtualRange(list);
        return Math.round((list.records.length - end) * this.virtualRowHeight);
    },

    _getVirtualScrollElement() {
        let el = this.rootRef.el;
        while (el && el !== document.body) {
            const overflowY = getComputedStyle(el).overflowY;
            if (overflowY === "auto" || overflowY === "scroll") {
                return el;
            }
            el = el.parentElement;
        }
        return null;
    },

    _scheduleVirtualUpdate() {
        if (this._virtualUpdateScheduled || !this.shouldVirtualize(this.props.list)) {
            return;
        }
        this._virtualUpdateScheduled = true;
        requestAnimationFrame(() => {
            this._virtualUpdateScheduled = false;
            this._updateVirtualWindow();
        });
    },

    _afterVirtualRender() {
        const active = this.shouldVirtualize(this.props.list);
        if (this.rootRef.el) {
            this.rootRef.el.classList.toggle("o_kq_list_virtual", active);
        }
        if (!active) {
            this._kqScrollRender = false;
            return;
        }
        this._kqScrollRender = false;
        this._measureVirtualRowHeight();
        this._updateVirtualWindow();
    },

    _measureVirtualRowHeight() {
        const tbody = this.tableRef.el && this.tableRef.el.querySelector("tbody");
        if (!tbody) {
            return;
        }
        const rows = tbody.querySelectorAll(".o_data_row");
        if (!rows.length) {
            return;
        }
        let total = 0;
        for (const row of rows) {
            total += row.getBoundingClientRect().height;
        }
        const average = total / rows.length;
        if (average > 0 && Math.abs(average - this.virtualRowHeight) > 0.5) {
            this.virtualRowHeight = average;
        }
    },

    _updateVirtualWindow() {
        const scrollEl = this._getVirtualScrollElement();
        const tbody = this.tableRef.el && this.tableRef.el.querySelector("tbody");
        if (!scrollEl || !tbody || !this.shouldVirtualize(this.props.list)) {
            return;
        }
        const total = this.props.list.records.length;
        const rowHeight = this.virtualRowHeight;
        const scrolled = Math.max(
            0,
            scrollEl.getBoundingClientRect().top - tbody.getBoundingClientRect().top
        );
        const firstVisible = Math.floor(scrolled / rowHeight);
        const visibleCount = Math.ceil(scrollEl.clientHeight / rowHeight);
        let start = Math.max(0, firstVisible - OVERSCAN_ROWS);
        if (start > 0 && start % 2 === 0) {
            start -= 1;
        }
        const end = Math.min(total, firstVisible + visibleCount + OVERSCAN_ROWS);
        if (start !== this.virtualState.start || end !== this.virtualState.end) {
            this._kqScrollRender = true;
            this.virtualState.start = start;
            this.virtualState.end = end;
        }
    },
});
