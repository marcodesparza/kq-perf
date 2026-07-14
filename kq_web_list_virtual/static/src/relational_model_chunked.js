import { toRaw } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { DynamicRecordList } from "@web/model/relational_model/dynamic_record_list";
import { ListController } from "@web/views/list/list_controller";

const SYNC_CHUNK = 1000;
const ASYNC_CHUNK = 2000;

patch(ListController.prototype, {
    setup() {
        super.setup(...arguments);
        if ((session.kq_list_virtual_threshold ?? 0) && !this.editable) {
            this.model._kqChunkOk = true;
        }
    },
});

patch(DynamicRecordList.prototype, {
    _setData(data) {
        this._kqGen = (this._kqGen || 0) + 1;
        const threshold = session.kq_list_virtual_threshold ?? 0;
        const canChunk =
            threshold &&
            this.model._kqChunkOk &&
            data.records.length > Math.max(threshold, SYNC_CHUNK);
        if (!canChunk) {
            this._kqChunksPending = false;
            return super._setData(data);
        }
        const gen = this._kqGen;
        const rows = data.records;
        this.records = rows
            .slice(0, SYNC_CHUNK)
            .map((r) => this._createRecordDatapoint(r));
        this._updateCount(data);
        this._selectDomain(this.isDomainSelected);
        this._kqChunksPending = true;
        let offset = SYNC_CHUNK;
        const appendNext = () => {
            if (gen !== this._kqGen) {
                return;
            }
            const isRoot = toRaw(this.model.root) === toRaw(this);
            const size = isRoot ? ASYNC_CHUNK : rows.length - offset;
            const chunk = rows
                .slice(offset, offset + size)
                .map((r) => this._createRecordDatapoint(r));
            if (this.isDomainSelected) {
                chunk.forEach((r) => (r.selected = true));
            }
            offset += size;
            if (offset >= rows.length) {
                this._kqChunksPending = false;
            }
            this.records.push(...chunk);
            if (offset < rows.length) {
                setTimeout(appendNext);
            }
        };
        setTimeout(appendNext);
    },
});
