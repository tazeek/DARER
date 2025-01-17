import json
import torch
import torch.nn as nn
import torch.nn.functional as F

from nn.encode import BiGraphEncoder
from nn.decode import RelationDecoder, LinearDecoder

from utils.help import ReferMetric
from utils.dict import PieceAlphabet
from utils.load import WordAlphabet, LabelAlphabet
from utils.help import expand_list, noise_augment
from utils.help import nest_list, iterable_support
from utils.load import build_embedding_matrix
import copy

class TaggingAgent(nn.Module):

    def __init__(self,
                 word_vocab: WordAlphabet,
                 random_wordvec: bool,
                 data_dir: str,
                 piece_vocab: PieceAlphabet,
                 sent_vocab: LabelAlphabet,
                 act_vocab: LabelAlphabet,
                 adj_vocab: LabelAlphabet,
                 adj_full_vocab: LabelAlphabet,
                 adj_id_vocab: LabelAlphabet,
                 embedding_dim: int,
                 hidden_dim: int,
                 num_layer: int,
                 dropout_rate: float,
                 use_linear_decoder: bool,
                 pretrained_model: str,
                 rgcn_num_base: int,
                 stack_num: int,
                 margin_coefficient: int):

        super(TaggingAgent, self).__init__()

        self._piece_vocab = piece_vocab
        self._pretrained_model = pretrained_model

        self._word_vocab = word_vocab
        self._sent_vocab = sent_vocab
        self._act_vocab = act_vocab
        self._adj_vocab = adj_vocab
        self._adj_full_vocab = adj_full_vocab
        self._adj_id_vocab = adj_id_vocab

        self.lbda = margin_coefficient

        print("Before building word vectors")        
        
        if not random_wordvec:
            if 'mastodon' in data_dir:
                ds_n = 'mastodon'
            if 'daily' in data_dir:
                ds_n = 'dailydialog'
            embedding_matrix = build_embedding_matrix(
                    word2idx=self._word_vocab._elem_to_idx,
                    embed_dim= 300,
                    dat_fname='{0}_{1}_embedding_matrix.dat'.format(str(300), ds_n))
            word_embedding_matrix = nn.Embedding.from_pretrained(torch.tensor(embedding_matrix, dtype=torch.float), freeze = False)

        else:
            word_embedding_matrix = nn.Embedding(len(word_vocab), embedding_dim)
        self._encoder = BiGraphEncoder(
            word_embedding_matrix,
            hidden_dim, dropout_rate, pretrained_model, rgcn_num_base
        )
        if use_linear_decoder:
            self._decoder = LinearDecoder(len(sent_vocab), len(act_vocab), hidden_dim)
        else:
            self._decoder = RelationDecoder(
                len(sent_vocab), len(act_vocab), hidden_dim,
                num_layer, dropout_rate, rgcn_num_base, stack_num
            )

        # Loss function
        self._criterion = nn.NLLLoss(reduction="sum")

    # Add for loading best model
    def set_load_best_missing_arg(self, pretrained_model):
        self._pretrained_model = pretrained_model
        self._encoder.add_missing_arg(pretrained_model)

    def set_load_best_missing_arg_mastodon(self, pretrained_model, layer=2):
        self._pretrained_model = pretrained_model
        self._encoder.add_missing_arg(pretrained_model)
        self._decoder.add_missing_arg(layer)

    def forward(self, input_h, len_list, adj, pad_adj_full_list, pad_adj_R_list, mask=None):
        encode_h = self._encoder(input_h, adj, pad_adj_full_list, mask)
        return self._decoder(encode_h, len_list, pad_adj_R_list)

    @property
    def sent_vocab(self):
        return self._sent_vocab

    @property
    def act_vocab(self):
        return self._act_vocab

    def _wrap_padding(self, dial_list, adj_list, adj_full_list, adj_id_list, use_noise):
        dial_len_list = [len(d) for d in dial_list]
        max_dial_len = max(dial_len_list)

        adj_len_list = [len(adj) for adj in adj_list]
        max_adj_len = max(adj_len_list)

        # add adj_full
        adj_full_len_list = [len(adj_full) for adj_full in adj_full_list]
        max_adj_full_len = max(adj_full_len_list)

        # add adj_I
        adj_id_len_list = [len(adj_I) for adj_I in adj_id_list]
        max_adj_id_len = max(adj_id_len_list)

        assert max_dial_len == max_adj_len, str(max_dial_len) + " " + str(max_adj_len)
        assert max_adj_full_len == max_adj_len, str(max_adj_full_len) + " " + str(max_adj_len)
        assert max_adj_id_len == max_adj_full_len, str(max_adj_id_len) + " " + str(max_adj_full_len)

        turn_len_list = [[len(u) for u in d] for d in dial_list]
        max_turn_len = max(expand_list(turn_len_list))

        turn_adj_len_list = [[len(u) for u in adj] for adj in adj_list]
        max_turn_adj_len = max(expand_list(turn_adj_len_list))

        # add adj_full
        turn_adj_full_len_list = [[len(u) for u in adj_full] for adj_full in adj_full_list]
        max_turn_adj_full_len = max(expand_list(turn_adj_full_len_list))

        # add adj_I
        turn_adj_id_len_list = [[len(u) for u in adj_I] for adj_I in adj_id_list]
        max_turn_adj_id_len = max(expand_list(turn_adj_id_len_list))

        pad_adj_list = []
        for dial_i in range(0, len(adj_list)):
            pad_adj_list.append([])

            for turn in adj_list[dial_i]:
                pad_utt = turn + [0] * (max_turn_adj_len - len(turn))
                pad_adj_list[-1].append(pad_utt)

            if len(adj_list[dial_i]) < max_adj_len:
                pad_dial = [[0] * max_turn_adj_len] * (max_adj_len - len(adj_list[dial_i]))
                pad_adj_list[-1].extend(pad_dial)

        pad_adj_full_list = []
        for dial_i in range(0, len(adj_full_list)):
            pad_adj_full_list.append([])

            for turn in adj_full_list[dial_i]:
                pad_utt = turn + [0] * (max_turn_adj_full_len - len(turn))
                pad_adj_full_list[-1].append(pad_utt)

            if len(adj_full_list[dial_i]) < max_adj_full_len:
                pad_dial = [[0] * max_turn_adj_full_len] * (max_adj_full_len - len(adj_full_list[dial_i]))
                pad_adj_full_list[-1].extend(pad_dial)

        pad_adj_id_list = []
        for dial_i in range(0, len(adj_id_list)):
            pad_adj_id_list.append([])

            for turn in adj_id_list[dial_i]:
                pad_utt = turn + [0] * (max_turn_adj_id_len - len(turn))
                pad_adj_id_list[-1].append(pad_utt)

            if len(adj_id_list[dial_i]) < max_adj_id_len:
                pad_dial = [[0] * max_turn_adj_id_len] * (max_adj_id_len - len(adj_id_list[dial_i]))
                pad_adj_id_list[-1].extend(pad_dial)

        pad_adj_R_list = []
        for dial_i in range(0, len(pad_adj_id_list)):
            pad_adj_R_list.append([])
            assert len(pad_adj_id_list[dial_i]) == len(pad_adj_full_list[dial_i])
            for i in range(len(pad_adj_full_list[dial_i])):
                full = pad_adj_full_list[dial_i][i]
                pad_utt_up = full + full
                pad_adj_R_list[-1].append(pad_utt_up)

            for i in range(len(pad_adj_full_list[dial_i])):
                full = pad_adj_full_list[dial_i][i]
                pad_utt_down = full + full
                pad_adj_R_list[-1].append(pad_utt_down)

        assert len(pad_adj_id_list[0]) * 2 == len(pad_adj_R_list[0]), pad_adj_R_list[0]
 

        pad_w_list, pad_sign = [], self._word_vocab.PAD_SIGN
        for dial_i in range(0, len(dial_list)):
            pad_w_list.append([])

            for turn in dial_list[dial_i]:
                if use_noise:
                    noise_turn = noise_augment(self._word_vocab, turn, 5.0)
                else:
                    noise_turn = turn
                pad_utt = noise_turn + [pad_sign] * (max_turn_len - len(turn))
                pad_w_list[-1].append(iterable_support(self._word_vocab.index, pad_utt))

            if len(dial_list[dial_i]) < max_dial_len:
                pad_dial = [[pad_sign] * max_turn_len] * (max_dial_len - len(dial_list[dial_i]))
                pad_w_list[-1].extend(iterable_support(self._word_vocab.index, pad_dial))

        cls_sign = self._piece_vocab.CLS_SIGN
        piece_list, sep_sign = [], self._piece_vocab.SEP_SIGN

        for dial_i in range(0, len(dial_list)):
            piece_list.append([])

            for turn in dial_list[dial_i]:
                seg_list = self._piece_vocab.tokenize(turn)
                piece_list[-1].append([cls_sign] + seg_list + [sep_sign])

            if len(dial_list[dial_i]) < max_dial_len:
                pad_dial = [[cls_sign, sep_sign]] * (max_dial_len - len(dial_list[dial_i]))
                piece_list[-1].extend(pad_dial)

        p_len_list = [[len(u) for u in d] for d in piece_list]
        max_p_len = max(expand_list(p_len_list))

        pad_p_list, mask = [], []
        for dial_i in range(0, len(piece_list)):
            pad_p_list.append([])
            mask.append([])

            for turn in piece_list[dial_i]:
                pad_t = turn + [pad_sign] * (max_p_len - len(turn))
                pad_p_list[-1].append(self._piece_vocab.index(pad_t))
                mask[-1].append([1] * len(turn) + [0] * (max_p_len - len(turn)))

        var_w_dial = torch.LongTensor(pad_w_list)
        var_p_dial = torch.LongTensor(pad_p_list)
        var_mask = torch.LongTensor(mask)
        var_adj_dial = torch.LongTensor(pad_adj_list)
        var_adj_full_dial = torch.LongTensor(pad_adj_full_list)
        var_adj_R_dial = torch.LongTensor(pad_adj_R_list)

        if torch.cuda.is_available():
            var_w_dial = var_w_dial.cuda()
            var_p_dial = var_p_dial.cuda()
            var_mask = var_mask.cuda()
            var_adj_dial = var_adj_dial.cuda()
            var_adj_full_dial = var_adj_full_dial.cuda()
            var_adj_R_dial = var_adj_R_dial.cuda()

        return var_w_dial, var_p_dial, var_mask, turn_len_list, p_len_list, var_adj_dial, var_adj_full_dial, \
            pad_adj_full_list,  pad_adj_R_list


    def predict(self, utt_list, adj_list, adj_full_list, adj_id_list):
        var_utt, var_p, mask, len_list, _, var_adj, var_adj_full, pad_adj_full_list, pad_adj_R_list = \
            self._wrap_padding(utt_list, adj_list, adj_full_list, adj_id_list, False)
        if self._pretrained_model != "none":
            pred_sents, pred_acts = self.forward(var_p, len_list, var_adj, pad_adj_full_list, pad_adj_R_list, mask)
        else:
            pred_sents, pred_acts = self.forward(var_utt, len_list, var_adj, pad_adj_full_list, pad_adj_R_list, None)
        pred_sent, pred_act = pred_sents[-1], pred_acts[-1]
        trim_list = [len(l) for l in len_list]
        flat_sent = torch.cat(
            [pred_sent[i, :trim_list[i], :] for
             i in range(0, len(trim_list))], dim=0
        )
        flat_act = torch.cat(
            [pred_act[i, :trim_list[i], :] for
             i in range(0, len(trim_list))], dim=0
        )

        _, top_sent = flat_sent.topk(1, dim=-1)
        _, top_act = flat_act.topk(1, dim=-1)

        sent_list = top_sent.cpu().numpy().flatten().tolist()
        act_list = top_act.cpu().numpy().flatten().tolist()

        nest_sent = nest_list(sent_list, trim_list)
        nest_act = nest_list(act_list, trim_list)

        string_sent = iterable_support(
            self._sent_vocab.get, nest_sent
        )
        string_act = iterable_support(
            self._act_vocab.get, nest_act
        )
        return string_sent, string_act

    def measure(self, utt_list, sent_list, act_list, adj_list, adj_full_list, adj_id_list):
        var_utt, var_p, mask, len_list, _, var_adj, var_adj_full, pad_adj_full_list, pad_adj_R_list = \
            self._wrap_padding(utt_list, adj_list, adj_full_list, adj_id_list, True)

        flat_sent = iterable_support(
            self._sent_vocab.index, sent_list
        )
        flat_act = iterable_support(
            self._act_vocab.index, act_list
        )

        index_sent = expand_list(flat_sent)
        index_act = expand_list(flat_act)

        var_sent = torch.LongTensor(index_sent)
        var_act = torch.LongTensor(index_act)
        if torch.cuda.is_available():
            var_sent = var_sent.cuda()
            var_act = var_act.cuda()

        if self._pretrained_model != "none":
            pred_sents, pred_acts = self.forward(var_p, len_list, var_adj, pad_adj_full_list, pad_adj_R_list, mask)
        else:
            pred_sents, pred_acts = self.forward(var_utt, len_list, var_adj, pad_adj_full_list, pad_adj_R_list, None)
        trim_list = [len(l) for l in len_list]
        
        sent_loss = 0.0
        act_loss = 0.0
        sent_margin_loss = 0.0
        act_margin_loss = 0.0

        flat_preds_s, flat_preds_a = [], []
        #cross entropy loss
        for j in range(len(pred_sents)):
            flat_pred_s = torch.cat([pred_sents[j][i, :trim_list[i], :] for i in range(0, len(trim_list))], dim=0)
            flat_pred_a = torch.cat([pred_acts[j][i, :trim_list[i], :] for i in range(0, len(trim_list))], dim=0)
            
            flat_preds_s.append(flat_pred_s)
            flat_preds_a.append(flat_pred_a)
            sent_loss_item = self._criterion(F.log_softmax(flat_pred_s, dim=-1), var_sent)
            act_loss_item = self._criterion(F.log_softmax(flat_pred_a, dim=-1), var_act)

            sent_loss = sent_loss + sent_loss_item
            act_loss = act_loss + act_loss_item
        

        #margin loss
        for j in range(1, len(flat_preds_s)):
            sent_margin_loss_item = torch.sum(torch.index_select(F.relu(F.log_softmax(flat_preds_s[j-1], dim = -1) \
                - F.log_softmax(flat_preds_s[j],dim = -1)), 1, var_sent))
            act_margin_loss_item = torch.sum(torch.index_select(F.relu(F.log_softmax(flat_preds_a[j-1], dim = -1) \
                - F.log_softmax(flat_preds_a[j], dim = -1)), 1, var_act))

            sent_margin_loss = sent_margin_loss + sent_margin_loss_item
            act_margin_loss = act_margin_loss + act_margin_loss_item
       
        loss_sum = sent_loss + self.lbda * sent_margin_loss + act_loss + self.lbda * act_margin_loss
        #print(sent_loss, sent_margin_loss, act_loss, act_margin_loss, loss_sum)
        return loss_sum
