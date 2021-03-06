import argparse
import os

import pytorch_toolbelt.losses
import torch
from timm.utils import AverageMeter
from torch.optim import AdamW
from skimage.transform import  resize

from utils import CustomImageDataset, resizeFun,compute_dice_coefficient
from UnetBaseline import  UNet
from torch.cuda.amp import autocast, GradScaler
import  numpy as np
from skimage.util import montage
import matplotlib.pyplot as plt
from tqdm import  trange
def train_epoch(model, train_loader, optimizer, device, epoch, trainepochs,loss_fn):
    model.train()
    losses = AverageMeter()
    #     loss_fn = torch.nn.MSELoss()
    scaler = GradScaler()
    dice_coefficients = AverageMeter()

    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device).long()
        optimizer.zero_grad()
        with autocast():
            output = model(data)
            loss = loss_fn(output, target)  # * 1000
            output = torch.argmax(output,1)
        dice_coefficients.update(compute_dice_coefficient(output.detach().cpu().numpy(),target.cpu().numpy()),1)

        losses.update(loss.item(), data.size(0))
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        optimizer.step()
    return  losses.avg,dice_coefficients.avg

            
        # if epoch %20 == 0:
        #     np.save(file="100output.npy",arr=model(data).detach().cpu().numpy())
        #     np.save(file="100data.npy",arr=data.cpu().numpy())
        #     np.save(file="100target.npy",arr=target.cpu().numpy())

def val_epoch(model, train_loader, optimizer, device, epoch, trainepochs,loss_fn):
    model.eval()
    losses = AverageMeter()
    dice_coefficients = AverageMeter()
    output = None
    
    for batch_idx, (data, target) in enumerate(train_loader):
        batchSize = data.shape[0]
        targetResized=resizeFun(target,(batchSize,128,128,128))
        data,targetResized = data.to(device), torch.tensor(targetResized).to(device).long()
        #         optimizer.zero_grad()
        with torch.no_grad():
            output = model(data)
            loss = loss_fn(output, targetResized)
            losses.update(loss.item(), data.size(0))
            output = torch.argmax(output,1)
            output = resizeFun(output.cpu().numpy(),(batchSize,target.shape[1],target.shape[2],target.shape[3]))
            dice_coefficients.update(compute_dice_coefficient(output.astype(int),target.numpy()),1)

        if batch_idx % 5 == 0:
            if epoch%5==0:
                print("????????????")
                target  = targetResized.cpu().numpy()[0,:]
                output = output[0,:]
                # print(target.shape,output.shape)
                
                fig, ax1 = plt.subplots(1, 1, figsize = (40, 40),dpi=100)
                ax1.imshow(montage(output[20:len(output)-15]), cmap ='bone')
                fig.savefig('outPutImages/output_epoch{}.png'.format(epoch))
                fig, ax1 = plt.subplots(1, 1, figsize = (40, 40),dpi=100)
                ax1.imshow(montage(target[20:len(target)-15]), cmap ='bone')
                fig.savefig('outPutImages/target_epoch{}.png'.format(epoch))
                plt.close('all')
    return losses.avg,dice_coefficients.avg
def main():
    parser = argparse.ArgumentParser()
    arg = parser.add_argument
    arg("--batch-size", type=int, default=2)
    arg("--epochs", type=int, default=500)
    arg("--lr", type=float, default=0.0001)
    arg("--workers", type=int, default=2)
    arg("--model", type=str, default="UNet16")
    #     arg("--test_mode", type=str2bool, default="false",choices=[True,False])
    arg("--early_stopping", type=int, default=15)
    arg("--train_class", type=int, default=1, choices=[1, 2, 3, 4])
    arg("--optimizer", type=str, default="Adam")
    arg(
        "--device-ids",
        type=str,
        default="0",
        help="For example 0,1 to run on two GPUs",
    )
    args = parser.parse_args()
    if not os.path.exists("outPutImages"):
        try:
            os.makedirs("outPutImages")
        except:
            pass
    device = torch.device("cuda:%d" % 0)
    dataDirPath = "data/FLARE22_LabeledCase50-20220324T003930Z-001"
    # dataDirPath = "data/FLARE22_LabeledCase50"

    imgPaths = list(
        map(lambda x: os.path.join(dataDirPath, "images", x), os.listdir(os.path.join(dataDirPath, "images"))))
    labelPath = list(
        map(lambda x: os.path.join(dataDirPath, "labels", x), os.listdir(os.path.join(dataDirPath, "labels"))))
    # print(imgPaths)
    # print(labelPath)
    splitIndex = int(len(imgPaths) * 0.8)
    trainDataset = CustomImageDataset(CTImagePath=imgPaths[0:splitIndex],
                                      labelPath=labelPath[0:splitIndex],
                                      labelTransform=resizeFun,
                                      imgTransform=resizeFun)
    valDataset = CustomImageDataset(CTImagePath=imgPaths[splitIndex:],
                                      labelPath=labelPath[splitIndex:],
                                    imgTransform=resizeFun,
                                    # labelTransform=resizeFun,
                                      )

    print("total images:", len(trainDataset))
    train_loader = torch.utils.data.DataLoader(trainDataset, batch_size=args.batch_size, num_workers=args.workers,
                                               shuffle=True)
    val_loader = torch.utils.data.DataLoader(valDataset, batch_size=1, num_workers=args.workers,
                                             shuffle=False)
    model = UNet(in_dim=1, out_dim=14, num_filters=4)
    model.to(device)

    optimizer = AdamW(model.parameters(), lr=3e-4, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=150, gamma=0.25, last_epoch=-1)

    epochs = args.epochs
    valid_mse = 0
    valid_dice = 0 
    lossFun = torch.nn.CrossEntropyLoss()
    trainLosses = AverageMeter()
    trainDiceCoefficients = AverageMeter()
    validLosses = AverageMeter()
    validDiceCoefficients = AverageMeter()
    with trange(epochs) as t:
        # for i in t:
        for epoch in t:
            t.set_description('Epoch %i' % epoch)

            trainLossEpoch,trainDiceEpoch = train_epoch(model=model,
                        train_loader=train_loader,
                        optimizer=optimizer,
                        device=device,
                        epoch=epoch,
                        loss_fn=lossFun,
                        trainepochs=epochs)
            validLossEpoch,validDiceEpoch = val_epoch(model, val_loader, optimizer, device, epoch, epochs,lossFun)
            trainLosses.update(trainLossEpoch)
            trainDiceCoefficients.update(trainDiceEpoch)
            validLosses.update(validLossEpoch)
            validDiceCoefficients.update(validDiceEpoch)
            # t.set_postfix(All_train_Losses=trainLosses.avg,
            #               All_train_Dice=trainDiceCoefficients.avg,
            #               All_valid_Losses=validLosses.avg,
            #               All_valid_Dice=validDiceCoefficients.avg
            #               )
            t.set_postfix({"Train loss avg":trainLosses.avg,
                           "Train Dice resized":trainDiceCoefficients.avg,
                           "Valid loss avg":validLosses.avg,
                           "Valid dice full size":validDiceCoefficients.avg


            })

            if validDiceEpoch > valid_dice:
                torch.save(model.state_dict(), "best_baseline.pth")
                valid_dice=validDiceEpoch
            scheduler.step()


if __name__ == "__main__":
    main()