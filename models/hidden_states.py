import numpy as np
from tqdm import tqdm

import torch

############# Hidden States #############
def get_first_mask_loc(mask, shift=False):
    """
    return the location of the first pad token for the given ids, which corresponds to a mask value of 0
    if there are no pad tokens, then return the last location
    """
    # add a 0 to the end of the mask in case there are no pad tokens
    mask = torch.cat([mask, torch.zeros_like(mask[..., :1])], dim=-1)

    if shift:
        mask = mask[..., 1:]

    # get the location of the first pad token; use the fact that torch.argmax() returns the first index in the case of ties
    first_mask_loc = torch.argmax((mask == 0).int(), dim=-1)

    return first_mask_loc


def get_individual_hidden_states(model, batch_ids, layer=None, all_layers=True, token_idx=-1, model_type="encoder_decoder", use_decoder=False):
    """
    Given a model and a batch of tokenized examples, returns the hidden states for either 
    a specified layer (if layer is a number) or for all layers (if all_layers is True).
    
    If specify_encoder is True, uses "encoder_hidden_states" instead of "hidden_states"
    This is necessary for getting the encoder hidden states for encoder-decoder models,
    but it is not necessary for encoder-only or decoder-only models.
    """
    if use_decoder:
        assert "decoder" in model_type

    # forward pass
    with torch.no_grad():
        batch_ids = batch_ids.to(model.device)
        #print(f"Input batch_ids shape: {batch_ids['input_ids'].shape}")
        output = model(**batch_ids, output_hidden_states=True)

    # Debugging: Print keys and shapes from the model's output
    # print("Keys in output:", output.keys())
    # for key, value in output.items():
    #     if isinstance(value, torch.Tensor):
    #         print(f"Shape of {key}: {value.shape}")

    # get all the corresponding hidden states (which is a tuple of length num_layers)
    if use_decoder and "decoder_hidden_states" in output.keys():
        hs_tuple = output["decoder_hidden_states"]
    elif "encoder_hidden_states" in output.keys():
        hs_tuple = output["encoder_hidden_states"]
    else:
        hs_tuple = output["hidden_states"]

    # Debugging: Print the shape of the hidden states
    #print(f"Hidden state shape for batch_ids: {hs_tuple[0].shape}")  

    # just get the corresponding layer hidden states
    if all_layers:
        # stack along the last axis so that it's easier to consistently index the first two axes
        hs = torch.stack([h.squeeze().detach().cpu() for h in hs_tuple], axis=-1)  # (bs, seq_len, dim, num_layers)
    else:
        assert layer is not None
        hs = hs_tuple[layer].unsqueeze(-1).detach().cpu()  # (bs, seq_len, dim, 1)

    # we want to get the token corresponding to token_idx while ignoring the masked tokens
    if token_idx == 0:
        final_hs = hs[:, 0]  # (bs, dim, num_layers)
    else:
        # if token_idx == -1, then takes the hidden states corresponding to the last non-mask tokens
        # first we need to get the first mask location for each example in the batch
        assert token_idx < 0, print("token_idx must be either 0 or negative, but got", token_idx)
        mask = batch_ids["decoder_attention_mask"] if (model_type == "encoder_decoder" and use_decoder) else batch_ids["attention_mask"]
        first_mask_loc = get_first_mask_loc(mask).squeeze().cpu()
        final_hs = hs[torch.arange(hs.size(0)), first_mask_loc+token_idx]  # (bs, dim, num_layers)

    return final_hs


def get_all_hidden_states(model, dataloader, layer=None, all_layers=True,
                            token_idx=-1, model_type="encoder_decoder",
                            use_decoder=False, use_uncertainty=False):
    """
    Given a model, a tokenizer, and a dataloader, returns the hidden states (corresponding to a given position index) in all layers for all examples in the dataloader,
    along with the average log probs corresponding to the answer tokens

    The dataloader should correspond to examples *with a candidate label already added* to each example.
    E.g. this function should be used for "Q: Is 2+2=5? A: True" or "Q: Is 2+2=5? A: False", but NOT for "Q: Is 2+2=5? A: ".
    """
    all_pos_hs, all_neg_hs = [], []
    all_gt_labels = []
    if use_uncertainty:
        all_idk_hs = []

    model.eval()
    for batch in tqdm(dataloader):
        if use_uncertainty:
            neg_ids, pos_ids, idk_ids, _, _, _, gt_label = batch
        else:
            neg_ids, pos_ids, _, _, gt_label = batch

        neg_hs = get_individual_hidden_states(model, neg_ids, layer=layer, all_layers=all_layers, token_idx=token_idx, 
                                              model_type=model_type, use_decoder=use_decoder)
        pos_hs = get_individual_hidden_states(model, pos_ids, layer=layer, all_layers=all_layers, token_idx=token_idx, 
                                              model_type=model_type, use_decoder=use_decoder)

        if dataloader.batch_size == 1:
            neg_hs, pos_hs = neg_hs.unsqueeze(0), pos_hs.unsqueeze(0)

        # print(f'Batch neg_hs shape: {neg_hs.shape}')
        # print(f'Batch pos_hs shape: {pos_hs.shape}')

        #print(f'Before appending, all_neg_hs shape: {np.asarray(all_neg_hs).shape if all_neg_hs else None}')
        all_neg_hs.append(neg_hs)
        #print(f"After appending, individual neg_hs shape: {neg_hs.shape}, accumulated all_neg_hs shape: {np.asarray(all_neg_hs).shape}")

        # print(f'After appending, all_neg_hs shape: {np.asarray(all_neg_hs).shape}')
        # print(f'Before appending, all_pos_hs shape: {np.asarray(all_pos_hs).shape if all_pos_hs else None}')
        all_pos_hs.append(pos_hs)
        # print(f"After appending, individual pos_hs shape: {pos_hs.shape}, accumulated all_pos_hs shape: {np.asarray(all_pos_hs).shape}")
        # print(f'After appending, all_pos_hs shape: {np.asarray(all_pos_hs).shape}')

        all_gt_labels.append(gt_label)

        if use_uncertainty:
            idk_hs = get_individual_hidden_states(model, idk_ids, layer=layer, all_layers=all_layers, token_idx=token_idx, 
                                                  model_type=model_type, use_decoder=use_decoder)
            #print(f'Batch idk_hs shape: {idk_hs.shape}')
            if dataloader.batch_size == 1:
                idk_hs = idk_hs.unsqueeze(0)
                #print(f'Batch idk_hs shape after unsqueezing: {idk_hs.shape}')
            all_idk_hs.append(idk_hs)
    
    all_neg_hs = np.concatenate(all_neg_hs, axis=0)
    all_pos_hs = np.concatenate(all_pos_hs, axis=0)
    all_gt_labels = np.concatenate(all_gt_labels, axis=0)

    if use_uncertainty:
        all_idk_hs = np.concatenate(all_idk_hs, axis=0)
        # print(f'Concatenated all_neg_hs shape: {all_neg_hs.shape}')
        # print(f'Concatenated all_pos_hs shape: {all_pos_hs.shape}')
        # print(f'Concatenated all_idk_hs shape: {all_idk_hs.shape}')
        return all_neg_hs, all_pos_hs, all_idk_hs, all_gt_labels
    return all_neg_hs, all_pos_hs, all_gt_labels
